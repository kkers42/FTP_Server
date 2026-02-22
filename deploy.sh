#!/bin/bash
# ============================================================
# STL Hub - Deployment Script
# Run as root on the new VPS: bash deploy.sh
# ============================================================
set -e

APP_DIR="/opt/stl-hub"
APP_USER="stlhub"
FILES_DIR="/srv/stl-hub/files"

echo "=== STL Hub Deployment ==="

# 1. Update system & install dependencies
echo "[1/8] Installing system packages..."
apt-get update -qq
apt-get install -y python3 python3-pip python3-venv nginx vsftpd git curl

# 2. Create app user
echo "[2/8] Creating app user..."
id -u $APP_USER &>/dev/null || useradd -r -s /bin/bash -m $APP_USER

# 3. Create directories
echo "[3/8] Creating directories..."
mkdir -p $APP_DIR $FILES_DIR
chown -R $APP_USER:$APP_USER $APP_DIR $FILES_DIR

# 4. Copy app files
echo "[4/8] Copying application files..."
cp -r . $APP_DIR/
chown -R $APP_USER:$APP_USER $APP_DIR

# 5. Python venv + dependencies
echo "[5/8] Installing Python dependencies..."
python3 -m venv $APP_DIR/venv
$APP_DIR/venv/bin/pip install -q --upgrade pip
$APP_DIR/venv/bin/pip install -q -r $APP_DIR/requirements.txt

# 6. Set up .env if it doesn't exist
echo "[6/8] Setting up environment..."
if [ ! -f "$APP_DIR/.env" ]; then
    cp $APP_DIR/.env.example $APP_DIR/.env
    # Generate a random secret key
    SECRET=$(openssl rand -hex 32)
    sed -i "s/change_me_to_a_random_secret/$SECRET/" $APP_DIR/.env
    echo "⚠️  Created $APP_DIR/.env — please fill in your API keys!"
fi

# 7. vsftpd config
echo "[7/8] Configuring vsftpd..."
cat > /etc/vsftpd.conf << 'VSFTPD'
listen=YES
anonymous_enable=NO
local_enable=YES
write_enable=YES
local_umask=022
dirmessage_enable=YES
use_localtime=YES
xferlog_enable=YES
connect_from_port_20=YES
chroot_local_user=YES
allow_writeable_chroot=YES
secure_chroot_dir=/var/run/vsftpd/empty
pam_service_name=vsftpd
pasv_enable=YES
pasv_min_port=40000
pasv_max_port=40100
local_root=/srv/stl-hub/files
VSFTPD
systemctl enable vsftpd
systemctl restart vsftpd

# 8. Systemd service
echo "[8/8] Installing systemd service..."
cat > /etc/systemd/system/stl-hub.service << EOF
[Unit]
Description=STL Hub - 3D Print File Manager
After=network.target

[Service]
Type=simple
User=$APP_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=$APP_DIR/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8080 --workers 2
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable stl-hub
systemctl restart stl-hub
sleep 3
systemctl status stl-hub --no-pager | head -10

echo ""
echo "✅ STL Hub deployed!"
echo ""
echo "Next steps:"
echo "  1. Edit /opt/stl-hub/.env and fill in:"
echo "     - GOOGLE_CLIENT_ID"
echo "     - GOOGLE_CLIENT_SECRET"
echo "     - ANTHROPIC_API_KEY"
echo "     - OPENAI_API_KEY"
echo "     - APP_BASE_URL=http://187.77.218.25:8080"
echo ""
echo "  2. Add Google OAuth redirect URI in Google Cloud Console:"
echo "     http://187.77.218.25:8080/auth/google/callback"
echo ""
echo "  3. Restart after editing .env:"
echo "     systemctl restart stl-hub"
echo ""
echo "  4. Access the app at: http://187.77.218.25:8080"
