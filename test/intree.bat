#!/bin/bash

# Get public IP automatically
PUBLIC_IP=$(curl -s https://ifconfig.me)

# Run Flask app in background
python app.py &

# Wait for server to start

