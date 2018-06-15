import math
from functools import partial

import numpy as np
import tensorflow as tf


def is_constant(*args):
    return all(isinstance(a, (bool, int, float)) for a in args)


def is_numpy(*args):
    if is_constant(*args):
        return False
    return all(isinstance(
        a, (bool, int, float, np.ndarray, np.float32, np.int32, np.bool_))
        for a in args)


def is_tensor(*args):
    return any(isinstance(a, (tf.Tensor, tf.Variable)) for a in args)


def cast(value, dtype):
    if is_constant(value):
        return dtype(value)
    if is_numpy(value):
        dtypes = {
            float: np.float32,
            int: np.int32,
            bool: np.bool,
        }
        return np.cast[dtypes[dtype]](value)
    dtypes = {
        float: tf.float32,
        int: tf.int32,
        bool: tf.bool,
    }
    return tf.cast(value, dtypes[dtype])


def _constants_not_accepted(func):
    raise TypeError('{} does not accept constants as argmuents.'.format(func))


def where(bool_expr, true_value=None, false_value=None):
    args = [a for a in (bool_expr, true_value, false_value) if a is not None]
    if is_constant(*args):
        _constants_not_accepted(where)
    if is_numpy(*args):
        if true_value is None and false_value is None:
            return np.where(bool_expr)
        return np.where(bool_expr, true_value, false_value)
    return tf.where(bool_expr, true_value, false_value)


def nonzero(value):
    if is_constant(value):
        _constants_not_accepted(nonzero)
    if is_numpy(value):
        return np.nonzero(value)
    raise TypeError(
        'Tensorflow does not implement a function to compute non-zero values.')


def sum(value):
    if is_constant(value):
        _constants_not_accepted(where)
    if is_numpy(value):
        return np.sum(value)
    return tf.reduce_sum(value)


def mean(value):
    if is_constant(value):
        _constants_not_accepted(where)
    if is_numpy(value):
        return np.mean(value)
    return tf.reduce_mean(value)


def count(value):
    if is_constant(value):
        _constants_not_accepted(where)
    if is_numpy(value):
        return value.size
    return value.shape.num_elements()


def floor(value):
    if is_constant(value):
        return math.floor(value)
    if is_numpy(value):
        return np.floor(value)
    omap = {'Floor': 'Identity'}
    with tf.get_default_graph().gradient_override_map(omap):
        return tf.floor(value)


def ceil(value):
    if is_constant(value):
        return math.ceil(value)
    if is_numpy(value):
        return np.ceil(value)
    omap = {'Ceil': 'Identity'}
    with tf.get_default_graph().gradient_override_map(omap):
        return tf.ceil(value)


def round(value):
    if is_constant(value):
        return math.floor(value + 0.5)
    if is_numpy(value):
        return np.round(value)
    omap = {'Round': 'Identity'}
    with tf.get_default_graph().gradient_override_map(omap):
        return tf.round(value)


def equal(value1, value2):
    if is_constant(value1):
        return value1 == value2
    if is_numpy(value1):
        return value1 == value2
    omap = {'Round': 'Identity'}
    with tf.get_default_graph().gradient_override_map(omap):
        return tf.equal(value1, value2)


def greater_equal(value1, value2):
    if is_constant(value1):
        return value1 >= value2
    if is_numpy(value1):
        return value1 >= value2
    omap = {'Round': 'Identity'}
    with tf.get_default_graph().gradient_override_map(omap):
        return tf.greater_equal(value1, value2)


def abs(value):
    if is_constant(value):
        return abs(value)
    if is_numpy(value):
        return np.abs(value)
    return tf.abs(value)


def sqrt(value):
    if is_constant(value):
        return math.sqrt(value)
    if is_numpy(value):
        return np.sqrt(value)
    return tf.sqrt(value)


def log(value, base=None):
    if is_constant(value, base):
        return math.log(value, base)
    if is_numpy(value, base):
        return np.log(value) / np.log(base)
    return tf.log(value) / tf.log(cast(base, float))


def _binary_bool_operation(a, b, op):
    if is_constant(a, b):
        _constants_not_accepted('Element-wise operation')
    if is_numpy(a, b):
        return getattr(np, op)(a, b)
    return getattr(tf, op)(a, b)


logical_or = partial(_binary_bool_operation, op='logical_or')
logical_and = partial(_binary_bool_operation, op='logical_and')


def logical_not(value):
    if is_constant(value):
        _constants_not_accepted('Logical NOT')
    if is_numpy(value):
        return np.logical_not(value)
    return tf.logical_not(value)


def _clip(*args, min_max=None):
    if is_constant(*args):
        return min(*args) if min_max else max(*args)
    if is_numpy(*args):
        return np.minimum(*args) if min_max else np.maximum(*args)
    return tf.minimum(*args) if min_max else tf.maximum(*args)


min = partial(_clip, min_max=True)
max = partial(_clip, min_max=False)


def clip_by_value(tensor, minimum, maximum, transparent_backprop=False):
    if not is_tensor(tensor, minimum, maximum):
        return min(max(tensor, minimum), maximum)
    omap = {}
    if transparent_backprop:
        omap = {'Minimum': 'Identity', 'Maximum': 'Identity'}
    with tf.get_default_graph().gradient_override_map(omap):
        return tf.clip_by_value(tensor, minimum, maximum)


def top_k(tensor, k):
    if is_tensor(tensor):
        tensor = tf.reshape(tensor, [-1])
        topk = tf.nn.top_k(tensor, k)
        return tf.reduce_min(cast(topk.values, float))
    return sorted(tensor)[k]


def moments(tensor, axes):
    if is_tensor(tensor):
        return tf.nn.moments(tf.abs(tensor), axes=axes)
    mean = np.mean(tensor, axis=tuple(axes))
    var = np.var(tensor, axis=tuple(axes))
    return (mean, var)


def get_shape(tensor):
    if is_tensor(tensor):
        return tensor.get_shape()
    return tensor.shape


def stochastic_round(tensor, stochastic):
    value_floor = floor(tensor)
    value_ceil = ceil(tensor)
    if stochastic == 'naive':
        prob = 0.5
    elif stochastic == 'ulp':
        # tensor is scaled so ulp is 1
        prob = (tensor - value_floor)
    else:
        raise TypeError(
            'Stochastic argument {} not recognized.'.format(stochastic))
    if is_tensor(tensor):
        randoms = tf.random_uniform(shape=tensor.shape)
    else:
        randoms = np.random_uniform(shape=tensor.shape)
    round_up = cast(randoms > prob, float)
    round_down = cast(randoms <= prob, float)
    return value_ceil * round_up + value_floor * round_down
