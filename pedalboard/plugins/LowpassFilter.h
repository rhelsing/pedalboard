#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

namespace py = pybind11;

#include "../JucePlugin.h"

namespace Pedalboard {
template <typename SampleType>
class LowpassFilter : public JucePlugin<juce::dsp::IIR::Filter<SampleType>> {
public:
  void setCutoffFrequencyHz(float f) noexcept { cutoffFrequencyHz = f; }
  float getCutoffFrequencyHz() const noexcept { return cutoffFrequencyHz; }

  virtual void prepare(const juce::dsp::ProcessSpec &spec) override {
    JucePlugin<juce::dsp::IIR::Filter<SampleType>>::prepare(spec);
    this->getDSP().coefficients =
        juce::dsp::IIR::Coefficients<SampleType>::makeFirstOrderLowPass(
            spec.sampleRate, cutoffFrequencyHz);
  }

private:
  float cutoffFrequencyHz;
};

inline void init_lowpass(py::module &m) {
  py::class_<LowpassFilter<float>, Plugin>(
      m, "LowpassFilter",
      "Apply a first-order low-pass filter with a roll-off of 6dB/octave. "
      "The cutoff frequency will be attenuated by -3dB (i.e.: 0.707x as "
      "loud).")
      .def(py::init([](float cutoff_frequency_hz) {
             auto plugin = new LowpassFilter<float>();
             plugin->setCutoffFrequencyHz(cutoff_frequency_hz);
             return plugin;
           }),
           py::arg("cutoff_frequency_hz") = 50)
      .def("__repr__",
           [](const LowpassFilter<float> &plugin) {
             std::ostringstream ss;
             ss << "<pedalboard.Lowpass";
             ss << " cutoff_frequency_hz=" << plugin.getCutoffFrequencyHz();
             ss << " at " << &plugin;
             ss << ">";
             return ss.str();
           })
      .def_property("cutoff_frequency_hz",
                    &LowpassFilter<float>::getCutoffFrequencyHz,
                    &LowpassFilter<float>::setCutoffFrequencyHz);
}
}; // namespace Pedalboard
