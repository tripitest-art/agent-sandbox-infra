# ⚡ AMD RDNA2 GPU Tuning für LLM Inferenz (RX 6800 XT, Nobara)

Gemessener Stand des Gaming-PCs „nobara“ (Ryzen 7 5700X, 32 GB, RX 6800 XT 16 GB). Alle Zahlen
stammen aus Messungen vom 27.09. bis 01.10.2026 (Runbook `ollama-wol-setup`, `SETUP-LOG.md`).
Korrigiert am 01.10.2026: Die frühere Fassung nannte 150 W, −100 mV, einen Boot-Parameter und
ein Verbot von Flash Attention. Das hat sich nicht bestätigt (siehe unten).

---

## 1. Warum drosseln?

* **LLM-Generierung ist durch die VRAM-Bandbreite begrenzt**, nicht durch den Chiptakt. Ein
  niedrigerer Takt kostet deshalb nur wenig Token-Rate, spart aber Leistung und Wärme.
* **Anlass war ein 550-W-Netzteil**, das bei Lastspitzen der Karte (Steckdose bis 469 W)
  abschaltete. Mit neuem Netzteil bleibt die Drosselung auf Wunsch bestehen.

---

## 2. Messwerte

**Drosselung 2200 MHz / −30 mV / 255 W** (27.09., `bench_fa.py`, ctx 65536, 8k-Prompt, 3 Läufe):

| Metrik | ohne Drosselung | gedrosselt | Delta |
|---|---|---|---|
| Prompt | ~1300 tok/s | 1217 tok/s | −6,5 % |
| Generierung | ~56,9 tok/s | 55,4 tok/s | −2,6 % |
| GPU unter Last | 235–246 W | Ø 187 W, max. 202 W | ~−20 % |
| Steckdose (mit Monitor) | Spitzen 445–469 W | 321–353 W | |

**Qwen3-Coder 30B MoE (Q4_K_M, 18 GB)** mit FA + KV q8_0 (01.10., aktuelles Profil „Low“):

| Kontext | Prompt | Generierung | Speicher | GPU max. |
|---|---|---|---|---|
| 65536 | 918 tok/s | 36,6 tok/s | 22 GB (29 % CPU) | ~160 W |
| 32768 | 974 tok/s | 40,6 tok/s | 20 GB (23 % CPU) | ~163 W |
| 16384 | 1002 tok/s | 42,5 tok/s | 19 GB (20 % CPU) | ~163 W |

Das Modell ist größer als 16 GB VRAM, ein Teil läuft deshalb immer auf der CPU.

---

## 3. Overdrive freischalten – ohne Bootloader

`amdgpu` liegt auf Nobara nicht im initramfs, deshalb reicht eine modprobe-Option. Kein
Eingriff in GRUB/EFI nötig:

```bash
echo 'options amdgpu ppfeaturemask=0xfff7ffff' | sudo tee /etc/modprobe.d/amdgpu-overdrive.conf
sudo reboot
cat /sys/module/amdgpu/parameters/ppfeaturemask   # 0xfff7ffff
```

`0xfff7ffff` = Kernel-Standard `0xfff7bfff` plus nur das Overdrive-Bit (`PP_OVERDRIVE_MASK`
0x4000). **Nicht** `0xffffffff`: Das schaltet zusätzlich `PP_GFX_DCS_MASK` ein. Der Kernel meldet
danach „Overdrive is enabled“ und setzt Taint 4, das ist erwartet.

---

## 4. LACT (aktiv seit 30.09.2026: Profil „Low“)

`sudo dnf install lact && sudo systemctl enable --now lactd`. LACT setzt die Werte nach jedem Start.

* **Power-Limit: 255 W.** Das ist das **Minimum der Karte** (erlaubter Bereich 255–312 W).
  Werte darunter, z. B. 150 W, sind über sysfs/LACT nicht einstellbar.
* **Max. Core-Takt: 2100 MHz** (Werk: OD_SCLK 2449 MHz, Boost bis 2575)
* **Spannungs-Offset: −90 mV**
* **VRAM-Takt: 1040 MHz** (Bereich 674–1075)
* Eigene Lüfterkurve

Unter LLM-Last bleibt die Karte so bei ~160 W, deutlich unter dem 255-W-Limit.

Den früher hier beschriebenen festen Speicher-Takt (`power_dpm_force_performance_level=manual`,
`pp_dpm_mclk`) haben wir nie gemessen. Er hält den VRAM auch im Leerlauf auf Maximaltakt und ist
deshalb nicht empfohlen.

---

## 5. Ollama (systemd-Override)

Vorlage: [`sandbox/systemd/ollama-override.conf`](../sandbox/systemd/ollama-override.conf) →
`/etc/systemd/system/ollama.service.d/override.conf`, danach `daemon-reload` und Neustart.

### Flash Attention: muss AN sein
* `OLLAMA_KV_CACHE_TYPE=q8_0` **braucht** `OLLAMA_FLASH_ATTENTION=1`. Ohne FA lädt das Modell
  nicht (`quantized V cache requires flash_attn`, HTTP 500).
* Mit FA aus und KV f16 läuft Qwen3-Coder mit 344 / 17,8 tok/s statt 918 / 36,6 tok/s und
  braucht 29 statt 22 GB.
* Die frühere Warnung („Kernel-Panics, Ring Timeout“) ließ sich nicht reproduzieren: In allen
  Läufen gab es 0 Ring-Timeouts, Resets oder Page Faults (Ollama 0.34.4, FA an seit 27.09.).
