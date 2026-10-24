#!/bin/bash
set -e

# Base
sudo apt update
sudo apt install -y \
    python3-pip \
    python3-venv \
    adb \
    scrcpy \
    android-tools-adb \
    git \
    curl \
    unzip

# Java
sudo apt install -y openjdk-21-jdk

# Android SDK Command Line Tools
mkdir -p "$HOME/android-sdk/cmdline-tools"
cd "$HOME/android-sdk/cmdline-tools"

curl -L -o tools.zip \
https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip

unzip -o tools.zip

mv cmdline-tools latest

yes | "$HOME/android-sdk/cmdline-tools/latest/bin/sdkmanager" --licenses

"$HOME/android-sdk/cmdline-tools/latest/bin/sdkmanager" \
    "platform-tools" \
    "platforms;android-34" \
    "build-tools;34.0.0"

echo "NOTA iOS: simulador precisa de macOS"

cd ../server

python3 -m venv venv

./venv/bin/pip install \
    fastapi \
    "uvicorn[standard]" \
    websockets \
    requests
``
