# SPECTRE-CSI 🛡️⚡

**Wi-Fi Channel State Information (CSI) Presence & Motion Detector**

`SPECTRE-CSI` is a standalone, production-oriented CLI tool designed for human presence and motion detection using Wi-Fi CSI amplitude variations. It features a Cyberpunk terminal UI built with `rich`, fast signal processing via `numpy`, and supports live data ingestion from an ESP32 serial link.

---

## 🚀 Features

* **Dual-Mode Operation:** Run instantly via synthetic simulation or connect real ESP32 hardware.
* **Cyberpunk Terminal Dashboard:** Real-time metrics, presence states (`CLEAR`, `MINOR`, `HEAVY`), and ASCII waveform rendering powered by `rich`.
* **Signal Processing Core:** Uses rolling envelope analysis and Median Absolute Deviation (MAD) to filter noise and detect subtle human micro-motions (like breathing) vs. heavy motion (walking).
* **CSV Session Recording:** Export live telemetry for offline analysis.
* **Universal Cross-Distro Support:** Automated installation script supporting Debian, Ubuntu, Kali Linux, Arch Linux, Fedora, Alpine, and Termux (Android).

---

## 📦 Installation

Clone the repository and run the universal setup script on **any Linux distribution or Termux**:

```bash
git clone [https://github.com/ddidy/spectre-csi.git](https://github.com/ddidy/spectre-csi.git)
cd spectre-csi
chmod +x setup.sh
./setup.sh

