"""Small offline operator guide; no additional main navigation pages."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QScrollArea, QVBoxLayout, QWidget

from cct import __version__
from cct.device_guides import DEVICE_GUIDES
from .widgets import QComboBox, button, heading, label


TOPICS = {
    "Getting started": [
        "1. Select the devices in Connections. Alicat and LI-COR each offer manual operation without USB readings or commands. Open Connection options to import reviewed settings and connect.",
        "2. Pick a Core test (Zero, Adsorption, Breakthrough or Desorption), or the separate Repeating breakthrough cycles function.",
        "3. Use What will happen? or Review checks to see the steps and how the test ends.",
        "4. Start, watch the Live test screen, and add time-stamped observations when useful.",
        "5. Choose End test when finished. The app waits for S3 valve/heater acknowledgements, holds the reviewed vent route for 10 seconds, then saves. A hot column remains hot after recording ends.",
        "6. Review or export the finished record in Results. Missing readings remain gaps, not zero.",
        "Adsorption starts with air off. Breakthrough starts in bypass. Desorption starts with heat off. Use the controls shown in Live test; normal Core tests have no concentration cutoff.",
        "Connections shows one device name and USB dropdown per row. Select Operated manually — no USB control for the 146C and set Local/manual at its front panel. The app controls the other devices but never commands or shuts off the manually operated 146C.",
        "Connection options in the top bar contains profile import, cable confirmation, connect/disconnect, simulation and wiring instructions. Choosing an adapter alone never starts communication or a test.",
    ],
    "Using a different computer": [
        "The instruments stay connected to the hub. One control computer at a time plugs into its computer-facing USB cable.",
        "Install the app and the required adapter drivers on that computer. Bring the reviewed apparatus profile, not another computer's virtual environment.",
        "Import the reviewed profile using Connection options. Connections refreshes its USB dropdowns automatically while open. Saved unique adapter identities are matched to the new computer's port names.",
        "For first-time pairing, follow the physical cable labels. Adapter identification alone does not prove which instrument is behind the cable.",
        "Connect explicitly for protocol verification. Missing, duplicate or unexpected adapter identities are not guessed.",
        "Changing adapter assignments resets commissioning. Changing only the operating system's port number for the same verified adapter does not change the physical mapping.",
        "Never unplug during a test as a normal way to end it. Choose End test and wait for safe shutdown first. Reconnection does not resume the old test.",
    ],
    "Data and sharing": [
        "Each test has a unique local record containing settings, one-second measurements, raw received-frame events, timestamps and faults.",
        "Use Export CSV for analysis in other software. Use Open folder to copy a finalized test folder to another computer.",
        "Import completed test accepts a finalized run.sqlite folder. It makes a new local copy and never reconnects equipment or resumes controls.",
        "Running or unfinalized records cannot be imported. Existing records are never overwritten.",
        "Keep active recording on a local disk, not a network drive or cloud-sync folder. Back up completed records using the lab's approved process.",
        "Connection report is an optional local support ZIP with operational status and software versions. It excludes measurement data, sample/operator details, profiles and raw logs.",
    ],
    "Safety and recovery": [
        "Software readiness is not a substitute for a physical plumbing, calibration and shutdown check by a qualified operator.",
        "Do not assume electrical output acknowledgements prove a valve moved or that gas is flowing. Verify the physical apparatus separately.",
        "Review the actual gas source, vent destination, temperature limits and independent physical stop procedure before a hardware test.",
        "End test requests feed and heat off and holds the vent route for 10 seconds after acknowledgement. Monitor any remaining hot column and manually operated gas supplies. It is a controlled stop, not a certified physical emergency stop.",
        "If the app reports a fault, confirm the physical apparatus is safe, investigate the event, and fix the cause before starting a new test.",
        "Discovery only lists USB adapters. Connection sends the supported status/data queries; starting a test is a separate action.",
        "Core2 firmware and the particular 146C mapping, Alicat configuration and all valve routes require supervised commissioning before live operation.",
    ],
}


class HelpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("CCT Control — operator guide")
        self.resize(740, 680)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.addWidget(heading("Operator guide", f"Offline help · CCT Control {__version__}"))
        self.topics = QComboBox()
        self.topics.addItems(list(TOPICS) + [guide["title"] for guide in DEVICE_GUIDES.values()])
        layout.addWidget(self.topics)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        self.text = label("", None, True)
        self.text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body_layout.addWidget(self.text)
        body_layout.addStretch()
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)
        layout.addWidget(button("Close guide", self.accept))
        self.topics.currentTextChanged.connect(self.show_topic)
        self.show_topic(self.topics.currentText())

    def show_topic(self, topic):
        if topic in TOPICS:
            lines = TOPICS[topic]
        else:
            guide = next((value for value in DEVICE_GUIDES.values() if value["title"] == topic), {})
            lines = [guide.get("summary", ""), *guide.get("steps", []),
                     "READINGS: " + guide.get("reading", ""), "CONTROLS: " + guide.get("controls", ""), guide.get("notes", "")]
        self.text.setText("\n\n".join(lines))
