# ⚡ AMD RDNA2 GPU Tuning für LLM Inferenz (z. B. RX 6800 XT)

Dieses Dokument beschreibt das hardware- und treiberseitige Tuning für lokale LLM-Inferenz (Ollama, ROCm) auf AMD RDNA2-Grafikkarten (z. B. Radeon RX 6800 XT 16 GB VRAM) unter Linux (Nobara / Fedora / Ubuntu).

---

## 1. Das Kernproblem: Warum Standard-Gaming-Settings ineffizient sind

* **Flaschenhals VRAM-Bandbreite:** Bei der Ausführung großer autoregressiver Modelle (z. B. Qwen3-Coder 30B MoE) ist die Rechenleistung der GPU-Shader nicht der Engpass. Der limitierende Faktor ist, wie schnell Tokens aus dem VRAM in den Chip geschaufelt werden können (Memory-Bandwidth Bound).
* **Verschwendete Energie:** Im Standardzustand boostet die RX 6800 XT auf über 2400 MHz und zieht 280–300 Watt. Da die Shader jedoch auf den VRAM warten müssen, wird ein Großteil der Energie in ungenutzte Hitze umgewandelt.
* **Ergebnis mit Tuning:** Bei einer Reduzierung des Power-Limits auf 150 W und Core-Clock auf 2100 MHz bleibt die Token-Rate **zu 100 % identisch** (~50.2 Tokens/s), der Stromverbrauch sinkt jedoch um fast 50 %.

---

## 2. Benchmark-Vergleich (Qwen3-Coder 30B MoE)

| Metrik | Werkseinstellung | Mit 150W Tuning + Undervolt | Delta |
|---|---|---|---|
| **Maximaler Verbrauch (TBP)** | ~280 – 300 W | **140 – 150 W** | **-50 % Strom** |
| **Spannung (VDDGFX)** | ~1.150 mV | **~837 – 1.050 mV** | Deutlich kühler |
| **Core Clock** | 2.300 – 2.450 MHz | **2.050 – 2.100 MHz** | Effizient |
| **Token-Generierung** | **50.30 Tokens/s** | **50.24 Tokens/s** | **0 % Verlust** |

---

## 3. Schritt 1: Kernel-Overdrive freischalten (Boot-Parameter)

Damit Linux Takt- und Spannungsänderungen an AMD-Karten erlaubt, muss der Kernel-Parameter `amdgpu.ppfeaturemask=0xffffffff` gesetzt sein.

Unter Nobara / Fedora:
```bash
sudo sed -i 's/GRUB_CMDLINE_LINUX_DEFAULT="/GRUB_CMDLINE_LINUX_DEFAULT="amdgpu.ppfeaturemask=0xffffffff /' /etc/default/grub
sudo grub2-mkconfig -o /boot/grub2/grub.cfg 2>/dev/null || sudo update-grub
sudo reboot
```

Nach dem Reboot prüfen:
```bash
cat /sys/module/amdgpu/parameters/ppfeaturemask
# Muss 0xffffffff (oder 4294967295) ausgeben
```

---

## 4. Schritt 2: Tuning via LACT (Empfohlener Weg)

**LACT** (*Linux AMDGPU Control Tool*) ist der sicherste Weg auf RDNA2, da es das VBIOS ausliest und unzulässige Werte verhindert:

1. **Power:**
   * Power Limit (Cap): **150 W**
2. **Thermals & Clocks:**
   * Max Core Clock: **2100 MHz**
   * Voltage Offset: **-100 mV** (entspricht ca. 1050 mV statt 1150 mV)
   * VRAM Clock: Auf Maximum lassen
3. **Autostart:**
   * Oben **"Apply on boot"** aktivieren.

---

## 5. Besonderheit: Memory Timings auf Linux

Unter Windows bietet der AMD Adrenalin Treiber die Option *"Fast Timings" (Level 2)*. 
* Im Open-Source-Treiber (`amdgpu`) auf Linux ist diese Funktion **nicht im Sysfs exponiert**.
* Um Latenzen durch Speicher-Taktsprünge dennoch zu eliminieren, wird der Memory Performance Level auf Maximallast verriegelt:
```bash
echo manual > /sys/class/drm/card0/device/power_dpm_force_performance_level
echo 3 > /sys/class/drm/card0/device/pp_dpm_mclk
```

---

## 6. Ollama Service-Tuning (systemd)

Um große Modelle mit 64k Kontextfenster im 16 GB VRAM zu halten:

```bash
sudo mkdir -p /etc/systemd/system/ollama.service.d
sudo cp sandbox/systemd/ollama-override.conf /etc/systemd/system/ollama.service.d/override.conf
sudo systemctl daemon-reload
sudo systemctl restart ollama
```

### ⚠️ Lebenswichtige Warnung zu Flash Attention auf AMD RDNA2
* `OLLAMA_KV_CACHE_TYPE=q8_0`: **Dringend empfohlen.** Quantisiert den Kontextverlauf auf 8-Bit und spart bis zu 75 % Cache-RAM.
* `OLLAMA_FLASH_ATTENTION=1`: **STRENG VERBOTEN auf RDNA2.** Führt mit ROCm zu reproduzierbaren Kernel-Panics (`amdgpu Ring Timeout`), GPU-Resets und Endlos-Schleifen beim WoL-Boot.
