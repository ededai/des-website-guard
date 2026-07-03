DEVICES = {
    "desktop":   {"width": 1440, "height": 900,  "user_agent": None,                                                                          "label": "Desktop 1440"},
    "laptop":    {"width": 1280, "height": 800,  "user_agent": None,                                                                          "label": "Laptop 1280"},
    # is_mobile/has_touch/device_scale_factor make Playwright emulate a real
    # device instead of a narrow desktop window — without them, mobile-only
    # bugs (touch menus, DPR-dependent layout) never reproduce in sweeps.
    "tablet":    {"width": 768,  "height": 1024, "user_agent": "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15",          "label": "iPad 768",   "is_mobile": True, "device_scale_factor": 2},
    "phone_ios": {"width": 390,  "height": 844,  "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15", "label": "iPhone 14",  "is_mobile": True, "device_scale_factor": 3},
    "phone_and": {"width": 412,  "height": 915,  "user_agent": "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36",                 "label": "Pixel 7",    "is_mobile": True, "device_scale_factor": 2.625},
}
