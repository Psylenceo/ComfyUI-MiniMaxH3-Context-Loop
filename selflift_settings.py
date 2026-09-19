"""Lightweight lift controls and backward-compatible saved-hunt identities."""

LIFT_DEFAULTS = {"lowres_scale": 0.5, "rho": 0.0, "w_min": 0.5, "w_max": 1.0}


def lift_settings(settings):
    values = {}
    for name, default in LIFT_DEFAULTS.items():
        try:
            values[name] = float(settings.get(name, default))
        except (TypeError, ValueError):
            raise ValueError(f"SelfLift {name} must be a number.") from None
    if not 0.25 <= values["lowres_scale"] <= 1.0:
        raise ValueError("SelfLift lowres_scale must be between 0.25 and 1.0.")
    if not 0.0 <= values["rho"] <= 1.0:
        raise ValueError("SelfLift rho must be between 0 and 1; 0 disables pixel/VAE correction.")
    if not 0.0 <= values["w_min"] <= values["w_max"] <= 1.0:
        raise ValueError("SelfLift weights must satisfy 0 <= w_min <= w_max <= 1.")
    return values


def canonical_settings(settings):
    # Appending default-valued widgets must not invalidate old saved batches.
    # Keep all legacy fields as-is; only normalize the newly exposed controls.
    values = lift_settings(settings)
    result = {key: value for key, value in settings.items() if key not in LIFT_DEFAULTS}
    result.update({key: value for key, value in values.items() if value != LIFT_DEFAULTS[key]})
    return result
