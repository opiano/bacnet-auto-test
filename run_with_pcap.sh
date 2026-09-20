#!/bin/bash
# run_with_pcap.sh - Run any test command while capturing BACnet traffic to a PCAP file

if [ $# -eq 0 ]; then
    echo "Usage: ./run_with_pcap.sh <test_command>"
    echo "Example: ./run_with_pcap.sh python property_read_test.py --config config/property-read.yaml"
    echo "Example: ./run_with_pcap.sh python property_write_test.py --first-per-type"
    exit 1
fi

mkdir -p reports
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
PCAP_FILE="reports/bacnet_${TIMESTAMP}.pcap"
FILTER="udp port 47808 or udp port 47809"

echo "=========================================================="
echo "[*] Starting packet capture..."
echo "    File:   $PCAP_FILE"
echo "    Filter: $FILTER"
echo "=========================================================="

# Check for tcpdump or tshark and determine if sudo is needed
SUDO_CMD=""
if command -v tcpdump &> /dev/null; then
    if ! tcpdump -D &> /dev/null; then
        SUDO_CMD="sudo"
    fi
    $SUDO_CMD tcpdump -i any -nn "$FILTER" -w "$PCAP_FILE" &> /dev/null &
    CAP_PID=$!
elif command -v tshark &> /dev/null; then
    if ! tshark -D &> /dev/null; then
        SUDO_CMD="sudo"
    fi
    $SUDO_CMD tshark -i any -f "$FILTER" -w "$PCAP_FILE" &> /dev/null &
    CAP_PID=$!
else
    echo "Error: Neither tcpdump nor tshark is installed."
    echo "Install with: sudo apt install -y tcpdump"
    exit 1
fi

# Ensure capture process has started
sleep 1

# Run the user-supplied test command
echo "[*] Running: $@"
echo "----------------------------------------------------------"
"$@"
TEST_EXIT_CODE=$?
echo "----------------------------------------------------------"

# Stop packet capture (SIGINT allows tcpdump to flush buffers cleanly)
echo "[*] Stopping packet capture..."
if [ -n "$SUDO_CMD" ]; then
    sudo kill -2 $CAP_PID 2>/dev/null || sudo kill $CAP_PID 2>/dev/null
else
    kill -2 $CAP_PID 2>/dev/null || kill $CAP_PID 2>/dev/null
fi
wait $CAP_PID 2>/dev/null

# Allow non-root users to read the pcap file
if [ -n "$SUDO_CMD" ]; then
    sudo chmod 644 "$PCAP_FILE" 2>/dev/null
else
    chmod 644 "$PCAP_FILE" 2>/dev/null
fi

echo "=========================================================="
echo "[+] Packet capture saved: $PCAP_FILE"
echo "    Download to Windows PC via PowerShell:"
echo "    scp pi@<PI_IP>:$(pwd)/$PCAP_FILE ."
echo "=========================================================="

exit $TEST_EXIT_CODE
