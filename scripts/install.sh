#!/usr/bin/env bash
set -Eeuo pipefail

[[ ${EUID} -eq 0 ]] || { echo 'Run with sudo: sudo ./scripts/install.sh' >&2; exit 1; }
grep -aq 'Raspberry Pi 5' /proc/device-tree/model 2>/dev/null || { echo 'This installer is for Raspberry Pi 5 only.' >&2; exit 1; }

repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
app_user=${SUDO_USER:-}
[[ -n ${app_user} && ${app_user} != root ]] || { echo 'Run sudo from the desktop/console user who will run the SDR UI.' >&2; exit 1; }
id "${app_user}" >/dev/null
[[ ${app_user} =~ ^[a-z_][a-z0-9_-]*$ ]] || { echo 'Unsupported local user name.' >&2; exit 1; }
app_uid=$(id -u "${app_user}")

krel=$(uname -r)
boot_dir=/boot/firmware
[[ -d ${boot_dir} ]] || boot_dir=/boot
config_txt=${boot_dir}/config.txt
overlays_dir=${boot_dir}/overlays
module_dst=/lib/modules/${krel}/kernel/drivers/gpu/drm/panel/panel-sitronix-st7701.ko
state_dir=/var/lib/ituner-sdr

[[ -f ${config_txt} && -d ${overlays_dir} && -f ${module_dst} ]] || { echo 'Required Raspberry Pi boot/kernel paths are missing.' >&2; exit 1; }
echo 'Installing display driver, GT911 touch support, OpenGL SDR UI, and boot services. A reboot will be required.'
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  build-essential raspberrypi-kernel-headers device-tree-compiler \
  ffmpeg python3-pygame python3-opengl python3-pil pipewire-audio wireplumber

install -d -m 0755 /usr/local/src/ituner-sdr-display /opt/ituner-sdr/UI /usr/local/lib/ituner-sdr "${state_dir}"
install -m 0644 "${repo_dir}/display-driver/driver/Makefile" "${repo_dir}/display-driver/driver/panel-sitronix-st7701.c" /usr/local/src/ituner-sdr-display/
install -m 0644 "${repo_dir}/display-driver/overlays/yousee-yx45011act2-pi5-overlay.dts" /usr/local/src/ituner-sdr-display/
make -C "/lib/modules/${krel}/build" M=/usr/local/src/ituner-sdr-display modules
[[ -f ${state_dir}/panel-sitronix-st7701.${krel}.ko.original ]] || install -m 0644 "${module_dst}" "${state_dir}/panel-sitronix-st7701.${krel}.ko.original"
install -m 0644 /usr/local/src/ituner-sdr-display/panel-sitronix-st7701.ko "${module_dst}"
dtc -@ -I dts -O dtb -o "${overlays_dir}/yousee-yx45011act2.dtbo" /usr/local/src/ituner-sdr-display/yousee-yx45011act2-pi5-overlay.dts
install -m 0644 "${repo_dir}/touch-driver/overlays/gt911-camdisp1.dtbo" "${overlays_dir}/gt911-camdisp1.dtbo"
depmod -a "${krel}"

tmp_file=$(mktemp)
trap 'rm -f "${tmp_file}"' EXIT
awk '/^# BEGIN ITUNER SDR$/ {skip=1; next} /^# END ITUNER SDR$/ {skip=0; next} !skip {print}' "${config_txt}" >"${tmp_file}"
{
  cat "${tmp_file}"
  printf '\n# BEGIN ITUNER SDR\n'
  printf 'dtparam=i2c_csi_dsi=on\n'
  printf 'dtoverlay=vc4-kms-v3d\n'
  printf 'dtoverlay=yousee-yx45011act2\n'
  printf 'dtoverlay=gt911-camdisp1\n'
  printf 'disable_fw_kms_setup=1\n'
  printf '# END ITUNER SDR\n'
} >"${config_txt}"

install -m 0644 "${repo_dir}/UI/fmdx.py" "${repo_dir}/UI/kiwi_gl_display.py" "${repo_dir}/UI/kiwi_live_display_fb.py" "${repo_dir}/UI/kiwi_station_health.py" "${repo_dir}/UI/render_sdr_frontend_mockup.py" /opt/ituner-sdr/UI/
install -d -m 0755 /opt/ituner-sdr/UI/assets
install -m 0644 "${repo_dir}/UI/assets/waterfall-texture.png" /opt/ituner-sdr/UI/assets/
install -m 0755 "${repo_dir}/scripts/start-opengl.sh" /usr/local/lib/ituner-sdr/start-opengl.sh
install -m 0755 "${repo_dir}/scripts/touch-ready.sh" /usr/local/lib/ituner-sdr/touch-ready.sh
install -m 0755 "${repo_dir}/touch-driver/src/sdr_touch_dot_fb.py" /usr/local/lib/ituner-sdr/touch-test.py
install -m 0755 "${repo_dir}/scripts/configure.sh" /usr/local/sbin/ituner-sdr-configure
install -m 0755 "${repo_dir}/scripts/uninstall.sh" /usr/local/sbin/ituner-sdr-uninstall
install -m 0755 "${repo_dir}/scripts/touch-test.sh" /usr/local/bin/ituner-sdr-touch-test
[[ -f /etc/ituner-sdr.conf ]] || install -m 0644 "${repo_dir}/config/ituner-sdr.conf" /etc/ituner-sdr.conf
sed -e "s/__ITUNER_SDR_USER__/${app_user}/g" -e "s/__ITUNER_SDR_UID__/${app_uid}/g" "${repo_dir}/systemd/ituner-sdr.service" >/etc/systemd/system/ituner-sdr.service
install -m 0644 "${repo_dir}/systemd/ituner-sdr-touch-ready.service" /etc/systemd/system/ituner-sdr-touch-ready.service
sed "s/__ITUNER_SDR_USER__/${app_user}/g" "${repo_dir}/systemd/ituner-sdr-health.service" >/etc/systemd/system/ituner-sdr-health.service
usermod -aG video,input,render,audio "${app_user}"
loginctl enable-linger "${app_user}"
systemctl daemon-reload
systemctl enable ituner-sdr-touch-ready.service ituner-sdr.service ituner-sdr-health.service
echo 'Installation complete. Reboot now so the display and touch overlays can load: sudo reboot'
