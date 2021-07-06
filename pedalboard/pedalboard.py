import collections
import platform
import weakref
from functools import update_wrapper
from typing import List, Optional, Dict, Union, Tuple, Iterable

import numpy as np

from pedalboard_native import Plugin, process, AudioProcessorParameter


class Pedalboard(collections.MutableSequence):
    """
    A container for a chain of plugins, to use for processing audio.
    """

    def __init__(self, plugins: List[Optional[Plugin]], sample_rate: Optional[float] = None):
        for plugin in plugins:
            if plugin is not None:
                if not isinstance(plugin, Plugin):
                    raise TypeError(
                        "An object of type {} cannot be included in a {}.".format(
                            type(plugin), self.__class__.__name__
                        )
                    )
                if plugins.count(plugin) > 1:
                    raise ValueError(
                        "The same plugin object ({}) was included multiple times in a {}. Please"
                        " create unique instances if the same effect is required multiple times in"
                        " series.".format(plugin, self.__class__.__name__)
                    )
        self.plugins = plugins

        if sample_rate is not None and not isinstance(sample_rate, (int, float)):
            raise TypeError("sample_rate must be None, an integer, or a floating-point number.")
        self.sample_rate = sample_rate

    def __repr__(self) -> str:
        return "<{} plugins={} sample_rate={}>".format(
            self.__class__.__name__, repr(self.plugins), repr(self.sample_rate)
        )

    def __len__(self) -> int:
        return len(self.plugins)

    def __delitem__(self, index: int) -> None:
        self.plugins.__delitem__(index)

    def insert(self, index: int, value: Optional[Plugin]) -> None:
        if value is not None:
            if not isinstance(value, Plugin):
                raise TypeError(
                    "An object of type {} cannot be inserted into a {}.".format(
                        type(value), self.__class__.__name__
                    )
                )
            if value in self.plugins:
                raise ValueError(
                    "The provided plugin object ({}) already exists in this {}. Please"
                    " create unique instances if the same effect is required multiple times in"
                    " series.".format(value, self.__class__.__name__)
                )
        self.plugins.insert(index, value)

    def __setitem__(self, index: int, value: Optional[Plugin]) -> None:
        if value is not None:
            if not isinstance(value, Plugin):
                raise TypeError(
                    "An object of type {} cannot be added into a {}.".format(
                        type(value), self.__class__.__name__
                    )
                )
            if self.plugins.count(value) == 1 and self.plugins.index(value) != index:
                raise ValueError(
                    "The provided plugin object ({}) already exists in this {} at index {}. Please"
                    " create unique instances if the same effect is required multiple times in"
                    " series.".format(value, self.__class__.__name__, self.plugins.index(value))
                )
        self.plugins.__setitem__(index, value)

    def __getitem__(self, index: int) -> Optional[Plugin]:
        return self.plugins.__getitem__(index)

    def process(
        self,
        audio: np.ndarray,
        sample_rate: Optional[float] = None,
        buffer_size: Optional[int] = None,
    ) -> np.ndarray:
        if sample_rate is not None and not isinstance(sample_rate, (int, float)):
            raise TypeError("sample_rate must be None, an integer, or a floating-point number.")
        if buffer_size is not None:
            if not isinstance(buffer_size, (int, float)):
                raise TypeError("buffer_size must be None, an integer, or a floating-point number.")
            buffer_size = int(buffer_size)

        effective_sample_rate = sample_rate or self.sample_rate
        if effective_sample_rate is None:
            raise ValueError(
                (
                    "No sample rate available. `sample_rate` must be provided to either the {}"
                    " constructor or as an argument to `process`."
                ).format(self.__class__.__name__)
            )

        # pyBind11 makes a copy of self.plugins when passing it into process.
        kwargs = {"sample_rate": effective_sample_rate, "plugins": self.plugins}
        if buffer_size:
            kwargs["buffer_size"] = buffer_size
        return process(audio, **kwargs)

    # Alias process to __call__, so that people can call Pedalboards like functions.
    __call__ = process


FLOAT_SUFFIXES_TO_IGNORE = "x%*,."


def looks_like_float(s: str) -> bool:
    try:
        float(s.rstrip(FLOAT_SUFFIXES_TO_IGNORE))
        return True
    except ValueError:
        return False


def wrap_type(base_type):
    class WeakTypeWrapper(base_type):
        """
        A wrapper around `base_type` that allows adding additional
        accessors through a weak reference. Useful for syntax convenience.
        """

        def __new__(cls, value, *args, **kwargs):
            try:
                return base_type.__new__(cls, value)
            except TypeError:
                return base_type.__new__(cls)

        def __init__(self, *args, **kwargs):
            if 'wrapped' in kwargs:
                self._wrapped = weakref.ref(kwargs['wrapped'])
                del kwargs['wrapped']
            else:
                raise ValueError(
                    "WeakTypeWrapper({}) expected to be passed a 'wrapped' keyword argument."
                    .format(base_type)
                )
            try:
                super().__init__(*args, **kwargs)
            except TypeError:
                pass

        def __getattr__(self, name):
            wrapped = self._wrapped()
            if hasattr(wrapped, name):
                return getattr(wrapped, name)
            if hasattr(super(), "__getattr__"):
                return super().__getattr__(name)
            raise AttributeError("'{}' has no attribute '{}'".format(base_type.__name__, name))

        def __dir__(self) -> Iterable[str]:
            wrapped = self._wrapped()
            if wrapped:
                return list(dir(wrapped)) + list(super().__dir__())
            return super().__dir__()

    return WeakTypeWrapper


class WrappedBool(object):
    def __init__(self, value):
        if not isinstance(value, bool):
            raise TypeError(f"WrappedBool should be passed a boolean, got {type(value)}")
        self.__value = value

    def __repr__(self):
        return repr(self.__value)

    def __eq__(self, o: object) -> bool:
        return self.__value == o

    def __hash__(self) -> int:
        return hash(self.__value)

    def __bool__(self):
        return bool(self.__value)

    def __str__(self):
        return str(self.__value)

    def __getattr__(self, attr: str):
        return getattr(self.__value, attr)

    def __hasattr__(self, attr: str):
        return hasattr(self.__value, attr)


StringWithParameter = wrap_type(str)
FloatWithParameter = wrap_type(float)
BooleanWithParameter = wrap_type(WrappedBool)


TRUE_BOOLEANS = {'on', 'yes', 'true', 'enabled'}


def supplement_parameter(self: AudioProcessorParameter, search_steps: int = 1000):
    """
    VSTs and Audio Units don't always consistently give parameter values,
    types, or names - all we get is APIs that map float values on [0, 1]
    to strings, which may represent floats, strings, integers, enums, etc.

    AudioProcessorParameter exposes some attributes that can be
    implemented by plugins to give hints (i.e.: num_steps, allowed_values,
    is_discrete, etc) but not all plugins implement them properly.

    This method assigns additional properties to AudioProcessorParameter;
    we could do this in C++, but doing this in Python uses much less code
    and is (roughly) as performant as it should only run once per parameter.
    """
    if getattr(self, "__supplemented__", False):
        # Very quick sanity check: has the C++ parameter instance
        # changed identities from under us? This could happen if the C++
        # instance uses the same object in memory but changes its parameters.
        if self.__supplemented_name != self.name:
            raise NotImplementedError(
                "Parameter '{}' has been changed by the plugin instance. (It is now: {}) This may"
                " cause plugin parameter changes to throw undefined exceptions. Pedalboard is"
                " currently unable to accommodate plugins that change their parameters like this."
                .format(self.__supplemented_name, self)
            )
        else:
            return

    self.ranges: Dict[Tuple[float, float], Union[str, float, bool]] = {}

    start_of_range = 0
    text_value = None
    for x in range(0, search_steps + 1):
        raw_value = x / search_steps
        x_text_value = self.get_text_for_raw_value(raw_value)
        if text_value is None:
            text_value = x_text_value
        elif x_text_value != text_value:
            # End current range and start a new one
            self.ranges[(start_of_range, raw_value)] = text_value
            text_value = x_text_value
            start_of_range = raw_value
    self.ranges[(start_of_range, 1)] = text_value

    self.python_name = to_python_parameter_name(self)

    self.min_value = None
    self.max_value = None
    self.step_size = None
    self.approximate_step_size = None
    self.valid_values = list(self.ranges.values())
    self.type = str

    if all(looks_like_float(v) for v in self.ranges.values()):
        self.type = float
        self.ranges = {k: float(v.rstrip(FLOAT_SUFFIXES_TO_IGNORE)) for k, v in self.ranges.items()}
        self.min_value = min(self.ranges.values())
        self.max_value = max(self.ranges.values())

        sorted_values = sorted(self.ranges.values())
        first_derivative_steps = set(
            [round(abs(b - a), 8) for a, b in zip(sorted_values, sorted_values[1:])]
        )
        if len(first_derivative_steps) == 1:
            self.step_size = next(iter(first_derivative_steps))
        elif first_derivative_steps:
            self.approximate_step_size = sum(first_derivative_steps) / len(first_derivative_steps)
    elif len(self.valid_values) == 2 and (TRUE_BOOLEANS & {v.lower() for v in self.valid_values}):
        self.type = bool
        self.ranges = {k: v.lower() in TRUE_BOOLEANS for k, v in self.ranges.items()}
        self.min_value = False
        self.max_value = True
        self.step_size = 1

    self.range = self.min_value, self.max_value, self.step_size
    self._value_to_raw_value_ranges = {value: _range for _range, value in self.ranges.items()}

    def _get_raw_value_for(new_value) -> float:
        if self.type is float:
            try:
                new_value = float(new_value)
            except ValueError:
                raise ValueError(
                    "Value received for parameter '{}' ({}) must be a number".format(
                        self.python_name, new_value
                    )
                )
            if new_value < self.min_value or new_value > self.max_value:
                raise ValueError(
                    "Value received for parameter '{}' ({}) is out of range [{}{}, {}{}]".format(
                        self.python_name,
                        new_value,
                        self.min_value,
                        self.label,
                        self.max_value,
                        self.label,
                    )
                )
            plugin_reported_raw_value = self.get_raw_value_for_text(str(new_value))

            closest_diff = None
            closest_range_value = None
            for value, raw_value_range in self._value_to_raw_value_ranges.items():
                diff = new_value - value
                if closest_diff is None or abs(diff) < abs(closest_diff):
                    closest_range_value = raw_value_range
                    closest_diff = diff

            expected_low, expected_high = closest_range_value
            if (
                plugin_reported_raw_value < expected_low
                or plugin_reported_raw_value > expected_high
            ):
                # The plugin might have bad code in it when trying
                # to parse one of the string values it gave to us.
                # Let's use the range we had before:
                return expected_low
            else:
                return plugin_reported_raw_value

        elif self.type is str:
            if isinstance(new_value, (str, int, float, bool)):
                new_value = str(new_value)
            else:
                raise ValueError(
                    "Value received for parameter '{}' ({}) should be a string (or string-like),"
                    " but got an object of type: {}".format(
                        self.python_name, new_value, type(new_value)
                    )
                )
            if new_value not in self.valid_values:
                raise ValueError(
                    "Value received for parameter '{}' ({}) not in list of valid values: {}".format(
                        self.python_name, new_value, self.valid_values
                    )
                )
            plugin_reported_raw_value = self.get_raw_value_for_text(new_value)
            expected_low, expected_high = self._value_to_raw_value_ranges[new_value]
            if (
                plugin_reported_raw_value < expected_low
                or plugin_reported_raw_value > expected_high
            ):
                # The plugin might have bad code in it when trying
                # to parse one of the string values it gave to us.
                # Let's use the range we had before:
                return expected_low
            else:
                return plugin_reported_raw_value
        elif self.type is bool:
            if not isinstance(new_value, (bool, WrappedBool)):
                raise ValueError(
                    "Value received for parameter '{}' ({}) should be a boolean,"
                    " but got an object of type: {}".format(
                        self.python_name, new_value, type(new_value)
                    )
                )
            return 1.0 if new_value else 0.0
        else:
            raise ValueError(
                "Parameter has invalid type: {}. This should not be possible!".format(self.type)
            )

    self.get_raw_value_for = _get_raw_value_for
    self.__supplemented_name = self.name
    self.__supplemented__ = True
    return self


CPP_AUDIO_PROCESSOR_PARAMETER_REPR = AudioProcessorParameter.__repr__

"""
Monkey patch the C++ __repr__ of AudioProcessorParameter to add our Python functionality.
"""


def python_audio_processor_parameter_repr(self):
    cpp_repr_value = CPP_AUDIO_PROCESSOR_PARAMETER_REPR(self)
    if hasattr(self, '__supplemented__'):
        cpp_repr_value = cpp_repr_value.rstrip(">")
        if self.type is float:
            if self.step_size:
                return "{} value={} range=({}, {}, {})>".format(
                    cpp_repr_value,
                    self.string_value,
                    self.min_value,
                    self.max_value,
                    self.step_size,
                )
            elif self.approximate_step_size:
                return "{} value={} range=({}, {}, ~{})>".format(
                    cpp_repr_value,
                    self.string_value,
                    self.min_value,
                    self.max_value,
                    self.approximate_step_size,
                )
        elif self.type is str:
            return "{} value=\"{}\" ({} valid string value{})>".format(
                cpp_repr_value,
                self.string_value,
                len(self.valid_values),
                "" if len(self.valid_values) == 1 else "s",
            )
        elif self.type is bool:
            return "{} value={} boolean (\"{}\" and \"{}\")>".format(
                cpp_repr_value, self.string_value, self.valid_values[0], self.valid_values[1]
            )
    else:
        return cpp_repr_value


AudioProcessorParameter.__repr__ = python_audio_processor_parameter_repr


def to_python_parameter_name(parameter: AudioProcessorParameter) -> Optional[str]:
    if not parameter.name and not parameter.label:
        return None

    name = parameter.name.lower().strip()
    if parameter.label and not parameter.label.startswith(':'):
        name = "{} {}".format(name, parameter.label.lower())
    # Replace all non-alphanumeric characters with underscores
    name = [c if c.isalpha() or c.isnumeric() else "_" for c in name]
    # Remove any double-underscores:
    name = [a for a, b in zip(name, name[1:]) if a != b or b != "_"] + [name[-1]]
    # Remove any leading or trailing underscores:
    name = ''.join(name).strip('_')
    return name


class ExternalPlugin(object):
    def __set_initial_parameter_values__(
        self, parameter_values: Dict[str, Union[str, int, float, bool]] = {}
    ):
        # Manually clear the "__supplemented__" flag on any parameter
        # objects, as pybind11 may have reused the same memory:
        for parameter in self._parameters:
            parameter.__supplemented__ = False

        parameters = self.parameters
        for key, value in parameter_values.items():
            if key not in parameters:
                raise AttributeError(
                    "Parameter named \"{}\" not found. Valid options: {}".format(
                        key, ", ".join(self._parameter_weakrefs.keys())
                    )
                )
            setattr(self, key, value)

    @property
    def parameters(self) -> Dict[str, AudioProcessorParameter]:
        parameters = {}
        for parameter in self._parameters:
            supplement_parameter(parameter)
            if parameter.python_name:
                parameters[parameter.python_name] = parameter
        return parameters

    def __dir__(self):
        parameter_names = []
        for parameter in self._parameters:
            name = to_python_parameter_name(parameter)
            if name:
                parameter_names.append(name)
        return super().__dir__() + parameter_names

    def __getattr__(self, name: str):
        parameter = self.parameters.get(name)
        if parameter:
            string_value = parameter.string_value
            if parameter.type is float:
                return FloatWithParameter(
                    float(string_value.rstrip(FLOAT_SUFFIXES_TO_IGNORE)), wrapped=parameter
                )
            elif parameter.type is bool:
                return BooleanWithParameter(parameter.raw_value >= 0.5, wrapped=parameter)
            elif parameter.type is str:
                return StringWithParameter(str(string_value), wrapped=parameter)
            else:
                raise ValueError(
                    f"Parameter {parameter.python_name} has an unknown type. (Found"
                    f" '{parameter.type}')"
                )
        raise AttributeError(name)

    def __setattr__(self, name: str, value):
        parameter = self.parameters.get(name)
        if parameter:
            parameter.raw_value = parameter.get_raw_value_for(value)
        else:
            super().__setattr__(name, value)


try:
    from pedalboard_native import _VST3Plugin

    class VST3Plugin(_VST3Plugin, ExternalPlugin):
        def __init__(
            self,
            path_to_plugin_file: str,
            parameter_values: Dict[str, Union[str, int, float, bool]] = {},
        ):
            super().__init__(path_to_plugin_file)
            self.__set_initial_parameter_values__(parameter_values)


except ImportError:
    # We may be on a system that doesn't have native VST3Plugin support.
    pass

try:
    from pedalboard_native import _AudioUnitPlugin

    class AudioUnitPlugin(_AudioUnitPlugin, ExternalPlugin):
        def __init__(
            self,
            path_to_plugin_file: str,
            parameter_values: Dict[str, Union[str, int, float, bool]] = {},
        ):
            super().__init__(path_to_plugin_file)
            self.__set_initial_parameter_values__(parameter_values)


except ImportError:
    # We may be on a system that doesn't have native AudioUnitPlugin support.
    # (i.e.: any platform that's not macOS.)
    pass


AVAILABLE_PLUGIN_CLASSES = list(ExternalPlugin.__subclasses__())


def load_plugin(*args, **kwargs):
    if not AVAILABLE_PLUGIN_CLASSES:
        raise ImportError(
            "Pedalboard found no supported external plugin types in this installation ({}).".format(
                platform.system()
            )
        )
    exceptions = []
    for plugin_class in AVAILABLE_PLUGIN_CLASSES:
        try:
            return plugin_class(*args, **kwargs)
        except ImportError as e:
            exceptions.append(e)
        except Exception:
            raise
    else:
        tried_plugins = ", ".join([c.__name__ for c in AVAILABLE_PLUGIN_CLASSES])
        # Good error messages are important, okay?
        if len(AVAILABLE_PLUGIN_CLASSES) > 2:
            tried_plugins = ", or ".join(tried_plugins.rsplit(", ", 1))
        else:
            tried_plugins = " or ".join(tried_plugins.rsplit(", ", 1))
        raise ImportError(
            "Failed to load plugin as {}. Errors were:\n\t{}".format(
                tried_plugins,
                "\n\t".join(
                    [
                        "{}: {}".format(klass.__name__, exception)
                        for klass, exception in zip(AVAILABLE_PLUGIN_CLASSES, exceptions)
                    ]
                ),
            )
        )


if AVAILABLE_PLUGIN_CLASSES:
    update_wrapper(load_plugin, AVAILABLE_PLUGIN_CLASSES[0].__init__)
