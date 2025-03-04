#!/bin/bash

# Installer Chrome
echo "🔹 Installation de Google Chrome..."
mkdir -p /app/.apt
cd /app/.apt
wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
apt-get install -y ./google-chrome-stable_current_amd64.deb
ln -sf /usr/bin/google-chrome /app/.apt/usr/bin/google-chrome

# Installer ChromeDriver
echo "🔹 Installation de ChromeDriver..."
CHROME_VERSION=$(google-chrome --version | awk '{print $3}' | cut -d'.' -f1)
wget -q https://chromedriver.storage.googleapis.com/${CHROME_VERSION}.0.0/chromedriver_linux64.zip
unzip chromedriver_linux64.zip
mv chromedriver /app/.chromedriver/bin/chromedriver
chmod +x /app/.chromedriver/bin/chromedriver