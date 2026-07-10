# Optional dependencies

BaseBuddy runs without these; install only for the features you need.

## Face recognition (DeepFace)

```bash
pip install tf-keras
```

TensorFlow 2.16+ requires `tf-keras` separately. Without it you will see:

> DeepFace import error … Please run `pip install tf-keras`

Recognition features stay disabled until fixed.

## Alternative face backend (InsightFace)

```bash
pip install insightface onnxruntime
```

Optional; not required if using DeepFace.

## Clustering / analytics

```bash
pip install scikit-learn
```

Enables clustering helpers when `sklearn` is imported by optional modules.

## Plant segmentation (SAM)

```bash
pip install segment-anything
```

Download `sam_vit_b_01ec64.pth` via **Config → Model weights** or place in project root.

## 3D reconstruction engines (Multiview page)

The 3D Multiview page selects the best available engine automatically.
Classical SfM (SIFT) always works with no extra installs; the modern
feed-forward engines are far better and need one of:

```bash
# VGGT (CVPR 2025, recommended) — cameras + dense pointmaps in one pass
pip install git+https://github.com/facebookresearch/vggt.git
# Commercial deployments: request access to facebook/VGGT-1B-Commercial
# on Hugging Face, then set VGGT_MODEL=facebook/VGGT-1B-Commercial in .env

# Pi3 (ICLR 2026) — reference-free, most robust; weights are NON-COMMERCIAL
git clone https://github.com/yyfz/Pi3 ~/Projects/Pi3 && pip install -e ~/Projects/Pi3
# or set PI3_PATH=/path/to/Pi3 in .env

# DUSt3R (CVPR 2024) — legacy fallback
git clone https://github.com/naver/dust3r ~/Projects/dust3r
# or set DUST3R_PATH=/path/to/dust3r in .env
```

Model weights (1–2.5 GB) download from Hugging Face on first reconstruction.
A CUDA GPU is strongly recommended; CPU inference works but is slow.

## GPU / CUDA setup

**Your GPU:** RTX 2070 SUPER (8 GB) — fine for YOLO nano/small.

### 1. Fix the NVIDIA driver first (most common blocker)

If `nvidia-smi` shows **Driver/library version mismatch**, a driver upgrade did not finish. Kernel module and userspace libraries must match.

```bash
sudo dpkg --configure -a
sudo apt --fix-broken install
sudo apt install -y nvidia-driver-580
sudo reboot
```

After reboot:

```bash
nvidia-smi   # should show one driver version, no mismatch
```

Do **not** debug PyTorch until `nvidia-smi` is clean.

### 2. Install PyTorch matched to your driver (not CUDA 13)

Driver 575/580 supports up to CUDA **12.9**. Use PyTorch **cu126**, not cu130:

```bash
source venv/bin/activate
pip install torch==2.12.1+cu126 torchvision \
  --index-url https://download.pytorch.org/whl/cu126
```

Verify:

```bash
python -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

### 3. BaseBuddy

```bash
pip install -r requirements.txt
./run.sh
```

Config → enable GPU on camera profiles; use `yolov8n.pt` or `yolov8s.pt` on 8 GB VRAM.

**Note:** System `nvcc` (CUDA 12.0 toolkit) is for compiling C++ CUDA code only. PyTorch ships its own CUDA runtime via pip — you do not need to match `nvcc` to PyTorch.

## GPU notes (runtime)

- Local YOLO uses PyTorch CUDA when available
- `./run.sh` configures `LD_LIBRARY_PATH` for TensorRT/cuDNN when present in venv
- Use `./run.sh --safe` on low-memory hosts (limits cameras and AI FPS)

## Production security

In `.env` or `config.txt` when binding to `0.0.0.0`:

```bash
AUTH_ENABLE=true
ADMIN_PASSWORD=your-strong-password
SECRET_KEY=long-random-string
```
