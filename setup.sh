#!/bin/bash
# ==============================================================================
# SPECTRE-CSI :: Universal Linux Installer Script
# Works on Debian/Ubuntu/Kali, Arch, Fedora/RHEL, Alpine, and Termux (Android)
# ==============================================================================

set -e

GREEN='\033[0;32m'
NC='\033[0m'
YELLOW='\033[1;33m'
RED='\033[0;31m'

echo -e "${GREEN}[+] Starting SPECTRE-CSI Universal Installation...${NC}"

# 1. Detect Package Manager and Install Dependencies
if command -v apt &> /dev/null; then
    echo -e "${GREEN}[+] Detected Debian/Ubuntu/Kali Linux (apt)${NC}"
    sudo apt update -y
    sudo apt install -y python3 python3-pip python3-numpy python3-rich python3-serial
elif command -v pacman &> /dev/null; then
    echo -e "${GREEN}[+] Detected Arch Linux (pacman)${NC}"
    sudo pacman -Syu --noconfirm python python-numpy python-rich python-pyserial
elif command -v dnf &> /dev/null; then
    echo -e "${GREEN}[+] Detected Fedora/RHEL (dnf)${NC}"
    sudo dnf install -y python3 python3-numpy python3-rich python3-pyserial
elif command -v apk &> /dev/null; then
    echo -e "${GREEN}[+] Detected Alpine Linux (apk)${NC}"
    sudo apk add --no-cache python3 py3-pip py3-numpy py3-rich py3-serial
elif command -v pkg &> /dev/null; then
    echo -e "${GREEN}[+] Detected Termux (Android)${NC}"
    pkg update -y
    pkg install -y python python-pip
    pip install numpy rich pyserial --break-system-packages 2>/dev/null || pip install numpy rich pyserial
else
    echo -e "${YELLOW}[!] Unknown package manager. Falling back to pip installation...${NC}"
    pip3 install numpy rich pyserial --break-system-packages 2>/dev/null || pip3 install numpy rich pyserial
fi

# 2. Make Python Script Executable
chmod +x spectre_csi.py

# 3. Create Global System Command (Symlink)
if [ -w "/usr/local/bin" ] || command -v sudo &> /dev/null; then
    echo -e "${GREEN}[+] Creating global 'spectre-csi' executable link...${NC}"
    if [ -w "/usr/local/bin" ]; then
        ln -sf "$(pwd)/spectre_csi.py" /usr/local/bin/spectre-csi
    else
        sudo ln -sf "$(pwd)/spectre_csi.py" /usr/local/bin/spectre-csi
    fi
fi

echo -e "${GREEN}[✔] SPECTRE-CSI installation complete!${NC}"
echo -e "${YELLOW}[i] Run demo mode using:${NC} spectre-csi --demo ${YELLOW}or${NC} python3 spectre_csi.py --demo"
