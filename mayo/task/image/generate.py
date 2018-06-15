import tensorflow as tf

from mayo.util import ensure_list, pad_to_shape
from mayo.task.image.augment import Augment


class Preprocess(object):
    def __init__(
            self, system, mode, truth_keys, files, actions,
            before_shape, after_shape, moment=None):
        super().__init__()
        self.system = system
        self.mode = mode
        if mode not in ['test', 'train', 'validate']:
            raise ValueError(
                'Unrecognized preprocessing mode {!r}'.format(self.mode))
        self.truth_keys = truth_keys
        self.files = files
        self.actions = actions
        shape_to_tuple = lambda s: (
            s.get('height'), s.get('width'), s.get('channels'))
        self.before_shape = shape_to_tuple(before_shape)
        self.after_shape = shape_to_tuple(after_shape)
        self.moment = moment

    @staticmethod
    def _decode_jpeg(buffer, channels):
        with tf.name_scope(values=[buffer], name='decode_jpeg'):
            i = tf.image.decode_jpeg(buffer, channels=channels)
            return tf.image.convert_image_dtype(i, dtype=tf.float32)

    _max_objects = 100

    @classmethod
    def _parse_proto(cls, proto):
        int64 = tf.FixedLenFeature([], dtype=tf.int64, default_value=-1)
        int64s = tf.VarLenFeature(dtype=tf.int64)
        float32s = tf.VarLenFeature(dtype=tf.float32)
        string = tf.FixedLenFeature([], dtype=tf.string, default_value='')
        # dense features
        feature_map = {
            'image/encoded': string,
            'image/class/label': int64,
            'image/class/text': string,
            'image/object/bbox/label': int64s,
        }
        # bounding boxes
        bbox_keys = [
            'image/object/bbox/ymin', 'image/object/bbox/xmin',
            'image/object/bbox/ymax', 'image/object/bbox/xmax']
        for k in bbox_keys:
            feature_map[k] = float32s

        # parsing
        features = tf.parse_single_example(proto, feature_map)
        class_label = tf.cast(features['image/class/label'], dtype=tf.int32)
        bbox_label = tf.cast(
            features['image/object/bbox/label'].values, dtype=tf.int32)
        bbox_count = tf.shape(bbox_label)[0]
        bbox_label = pad_to_shape(bbox_label, [cls._max_objects], -1)

        # bbox handling
        # force the variable number of bounding boxes into the shape
        # [num_boxes, coords]
        bboxes = [features[k].values for k in bbox_keys]
        bboxes = tf.stack(axis=-1, values=bboxes)
        bboxes = pad_to_shape(bboxes, [cls._max_objects, 4], 0)

        # other
        encoded = features['image/encoded']
        text = features['image/class/text']
        return encoded, class_label, bboxes, bbox_count, bbox_label, text

    def _actions(self, key):
        return ensure_list(self.actions.get(key) or [])

    def _preprocess_images(self, name):
        image_string = tf.read_file(name)
        image = tf.image.decode_jpeg(
            image_string, channels=self.before_shape[-1])
        image = tf.image.convert_image_dtype(image, dtype=tf.float32)
        image = self.augment(image, ensure_shape='stretch')
        return image, name

    def _preprocess_records(self, serialized):
        # unserialize and prepocess image
        buffer, label, bbox, count, bbox_label, text = \
            self._parse_proto(serialized)
        # decode jpeg image
        channels = self.before_shape[-1]
        image = self._decode_jpeg(buffer, channels)
        # augment image
        augment_bbox = tf.expand_dims(bbox[:count], 0)
        augment = Augment(image, augment_bbox, self.after_shape, self.moment)
        actions = self._actions(self.mode) + self._actions('final_cpu')
        image = augment.augment(actions)
        values = [image]
        truth_map = {
            'class/label': label,
            'bbox': bbox,
            'bbox/label': bbox_label,
            'bbox/count': count,
            'text': text,
        }
        for key in self.truth_keys:
            values.append(truth_map[key])
        return values

    def augment(self, image, ensure_shape='fill'):
        # augment for validation
        augment = Augment(image, None, self.after_shape, self.moment)
        actions = self._actions(self.mode) + self._actions('final_cpu')
        actions += self._actions(self._actions('final_gpu'))
        return augment.augment(actions, ensure_shape=ensure_shape)

    def preprocess(self):
        # file names
        num_gpus = self.system.num_gpus
        batch_size = self.system.batch_size_per_gpu * num_gpus
        dataset = tf.data.Dataset.from_tensor_slices(self.files)
        if self.mode == 'train':
            # shuffle .tfrecord files
            dataset = dataset.shuffle(buffer_size=len(self.files))

        if self.mode == 'test':
            func = self._preprocess_images
        else:
            # tfrecord files to images
            dataset = dataset.flat_map(tf.data.TFRecordDataset)
            dataset = dataset.repeat()
            func = self._preprocess_records

        num_threads = self.system.preprocess.num_threads
        dataset = dataset.map(func, num_parallel_calls=num_threads)
        if self.mode in ['train', 'validate']:
            dataset = dataset.prefetch(num_threads * batch_size)
            if self.mode == 'train':
                buffer_size = min(1024, 10 * batch_size)
                dataset = dataset.shuffle(buffer_size=buffer_size)
            dataset = dataset.apply(
                tf.contrib.data.batch_and_drop_remainder(batch_size))
        else:
            dataset = dataset.batch(batch_size)
        # iterator
        iterator = dataset.make_one_shot_iterator()
        batch = iterator.get_next()
        batch_splits = list(zip(
            *(tf.split(each, num_gpus, axis=0) for each in batch)))

        # final preprocessing on gpu
        gpu_actions = self._actions('final_gpu')
        if gpu_actions:
            def augment(i):
                augment = Augment(i, None, self.after_shape, self.moment)
                return augment.augment(gpu_actions, ensure_shape=False)
            for gid, (images, *additional) in enumerate(batch_splits):
                with tf.device('/gpu:{}'.format(gid)):
                    # FIXME is tf.map_fn good for performance?
                    images = tf.map_fn(augment, images)
                batch_splits[gid] = [images] + additional
        return batch_splits
