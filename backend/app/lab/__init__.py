"""AI lab: detectors benchmarked on injected faults, leak localization, sensor value, and data-quality flags.

Everything here runs the real twin engine (no stored numbers). A fault run is a truth engine with an event injected and
a shadow engine that plays the operator's expectation, with its own demand-forecast error. Detectors only ever see the
readings and the expectation, never the truth or the fault tag.
"""

import warnings

# disabled sensors are nan on purpose; averaging over them is expected to be empty
warnings.filterwarnings("ignore", category=RuntimeWarning, module=r"app\.lab")
warnings.filterwarnings("ignore", message="Mean of empty slice")
warnings.filterwarnings("ignore", message="invalid value encountered")
warnings.filterwarnings("ignore", message="All-NaN slice")
