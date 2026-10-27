"""Operator-facing device help. Keep physical procedures out of widget code.

These describe supported operations, not a substitute for the instrument manual
or an approved apparatus-specific procedure. Discovery never operates equipment.
"""

DEVICE_GUIDES = {
    "core2": {
        "title": "Core2 controller + S3 output board",
        "summary": "Reads the column and ambient sensors, controls the local heater, and coordinates the S3 valves.",
        "steps": [
            "Keep the Core2 connected to the S3 through the existing UART cable; only Core2 connects to the USB hub.",
            "Use the reviewed cct-core2-usb-1 bridge firmware. The original sketch does not understand the app's commands.",
            "Connect explicitly and wait for compatible firmware, control ownership, fresh readings and S3 electrical readback.",
            "A qualified operator must verify the tubing, relay polarity, vent route and independent shutdown protection before commissioning.",
        ],
        "reading": "Column temperature (°C), ambient temperature (°C), ambient pressure (kPa), and output acknowledgements. Ambient pressure is not column pressure.",
        "controls": "Named routes, gas-source valves, local temperature target and controlled shutdown. The computer does not pulse the heater relay.",
        "notes": "115200 baud, 8N1. Local Stop and heartbeat-loss shutdown remain on Core2. Electrical output readback does not prove a valve physically moved. Firmware has been compiled, not uploaded by the app.",
        "reference_url": "",
    },
    "licor": {
        "title": "LI-COR LI-840A analyzer",
        "summary": "Measures CO₂ and water vapor in the gas reaching its inlet.",
        "steps": [
            "Use the instrument's RS-232 connection with the correct null-modem/crossover cable and a labelled USB adapter.",
            "Set 9600 baud, 8 data bits, no parity, 1 stop bit and no flow control.",
            "In the vendor setup tool, enable CO2, H2O and CELLTEMP output and keep XML stripping disabled.",
            "After Connect, wait for fresh measurements. The test waits for the configured analyzer-temperature band before progressing.",
        ],
        "reading": "CO₂ in ppm, H₂O in mmol/mol and analyzer cell temperature in °C. Incoming LI840 XML is checked; missing data are never filled with zero.",
        "controls": "The app reads an existing XML stream or requests current DATA. It does not automatically change calibration, zero/span coefficients or analyzer heating settings.",
        "notes": "The Zero test records a nitrogen baseline; it does not recalibrate the analyzer. Valid XML identifies a protocol family, not a unique instrument serial number.",
        "reference_url": "https://shop.licor.com/env/support/LI-840A/topics/ttp044-rs232-trouble.html",
    },
    "alicat": {
        "title": "Alicat B-500C flow meter",
        "summary": "Measures flow; this is not a flow-setpoint controller.",
        "steps": [
            "Verify the installed communication option and instrument-specific connector pinout. Do not assume a standard DB9 cable.",
            "For the supported legacy BASIS ASCII interface, verify the actual baud rate and unit address; defaults are 38400 baud and address A.",
            "Connect and confirm a labelled BASIS measurement containing temperature, SCCM or SLPM, and gas name.",
            "A qualified operator must confirm gas correction and the calibration reference before enabling gas-selection commands.",
        ],
        "reading": "Standard flow in sccm. SLPM is converted to sccm; labelled output is validated and unexpected status flags stop progression.",
        "controls": "Only verified Air/N₂ gas correction is supported. No flow setpoints or automatic tare are sent.",
        "notes": "BASIS variants and firmware can differ. A responding address and data format do not prove the exact instrument model or serial. The actual unit still needs a supervised check.",
        "reference_url": "https://store.alicat.com/products/b-500c",
    },
    "calibrator": {
        "title": "Thermo 146C gas calibrator",
        "summary": "Selects the reviewed Air/N₂ condition used by this apparatus.",
        "steps": [
            "Use the reviewed RS-232 connection and labelled adapter; the existing configuration is 9600 baud, 8N1, instrument ID 51.",
            "Connect for status readings first. Opening a connection does not select a gas or start delivery.",
            "Confirm which installed span setting corresponds to Air and which to N₂, then record distinct status readbacks for Air, N₂ and Off.",
            "Verify gas-source closure, instrument reconfiguration and reopening in a supervised commissioning session.",
        ],
        "reading": "Status text must match the configured identity and requested-state marker. An echoed command is not accepted as proof of gas selection.",
        "controls": "Only the established status, sel gas a span 0, sel gas a span 1 and sel gas off commands are allowed. No guessed N₂ command is supplied.",
        "notes": "The actual Air/N₂ mapping remains unconfirmed. Device pairing alone cannot commission this mapping, the plumbing or physical shutdown behavior.",
        "reference_url": "",
    },
}
