# Ascend 910B inference

This change adds a single-NPU ICL inference backend using `torch_npu` fused
attention, real-valued rotary embeddings, and the original sequence/window/ICL
attention restrictions. CUDA remains the default device. FlashAttention is
optional (`pip install .[cuda]`); install `requirements-npu.txt` for Ascend.
NPU training and distributed inference are outside this adaptation.

## Deployment snapshot

`deployment/ascend/` archives the tested workspace scripts, configuration,
download manifests, dependency records, and small validation reports. It does
not contain the Conda environment, checkpoint weights, videos, or caches.

The scripts use the original persistent workspace layout and absolute paths:

```text
/data5T/d00942093/miniforge3/
/data5T/d00942093/zero-wam/
    Zero-WAM/             # this Git checkout
    env/                  # Python 3.10 Conda environment
    models/
    data/
    scripts/
    activate.sh
    run_inference.sh
```

To restore the deployment snapshot into that workspace from the repository root:

```bash
cp -a deployment/ascend/. /data5T/d00942093/zero-wam/
cd /data5T/d00942093/zero-wam
mkdir -p logs models data outputs
```

This restores archived configuration and reports too. Follow the restored
`README.md` to create the environment, download the pinned files, verify them,
and run inference. Do not run the snapshot scripts directly inside
`deployment/ascend/`; their relative workspace paths assume they have been
restored. A different deployment path requires adjusting the activation and
launcher scripts. Dependency freeze files are records of the tested environment,
not standalone installers; use `requirements-npu.txt` and the current checkout.

## Validation recorded on 2026-09-29

- Linux ARM64, Ascend 910B3 64 GiB, CANN 9.1.0, PyTorch 2.9.0,
  torch_npu 2.9.0.post6, BF16.
- Official Robotwin checkpoint, 50 video/action denoising steps, two consecutive
  recorded-observation chunks, including the observation-cache update.
- Finite float32 actions of shape `(16, 4, 16)`; decoded video has 13 frames
  of 864 x 224 pixels.
- 13.96 / 13.49 seconds per chunk; peak allocated NPU memory 35.77 GiB.
- Two NPU numerical tests passed; eight relevant upstream tests passed and two
  CUDA-dependent tests were skipped.
- Local WebSocket health, reset, and one-step inference requests passed.

See [the recorded report](deployment/ascend/RUN_REPORT.md) and
[the inference metrics](deployment/ascend/outputs/place_object_scale/report.json).
These are measurements from the original run, not fresh simulation results.
RoboTwin simulation was unavailable on this ARM64 host because the required
SAPIEN package and graphics backend were unavailable. No task success rate or
real-robot execution is claimed.
