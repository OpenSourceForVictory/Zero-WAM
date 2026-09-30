"""Numerical checks for the Ascend inference backend (run on a real NPU)."""
import pytest
import torch
import torch.nn.functional as F

pytest.importorskip("torch_npu")
from wan_va.modules.icl_model import ICLAttentionBackend, _apply_rotary_emb
from wan_va.modules.model import WanRotaryPosEmbed

pytestmark = pytest.mark.skipif(not torch.npu.is_available(), reason="NPU required")


def test_npu_attention_preserves_icl_and_padding_masks():
    device = torch.device("npu:0")
    qt = torch.tensor([0, 1, 0, -1], dtype=torch.int32)
    kt = torch.tensor([2, 0, 1, -1, 2, 0], dtype=torch.int32)
    qs = torch.tensor([0, 0, 1, -1], dtype=torch.int32)
    ks = torch.tensor([0, 0, 0, -1, 1, 1], dtype=torch.int32)
    qf = torch.tensor([4, 4, 4, -1], dtype=torch.int32)
    kf = torch.tensor([0, 3, 0, -1, 0, 4], dtype=torch.int32)
    expected = torch.tensor([
        [1, 1, 0, 0, 0, 0],
        [0, 1, 0, 0, 0, 0],
        [0, 0, 0, 0, 1, 1],
        [0, 0, 0, 0, 0, 0],
    ], dtype=torch.bool)
    mask = ICLAttentionBackend.build_self_mask(
        *[t.to(device) for t in (qt, kt, qs, ks, qf, kf)],
        2, device,
    )
    torch.testing.assert_close(mask.cpu(), expected)
    torch.manual_seed(0)
    q = torch.randn(1, 4, 2, 128).bfloat16()
    k = torch.randn(1, 6, 2, 128).bfloat16()
    v = torch.randn_like(k)
    actual = ICLAttentionBackend.apply(
        q.to(device), k.to(device), v.to(device),
        cross_attention=False, block_mask=mask,
    ).cpu()
    ref = F.scaled_dot_product_attention(
        q.float().transpose(1, 2), k.float().transpose(1, 2),
        v.float().transpose(1, 2), attn_mask=expected,
    ).transpose(1, 2)
    torch.testing.assert_close(actual.float(), ref, atol=0.025, rtol=0.025)
    assert torch.count_nonzero(actual[:, -1]) == 0
    cross = ICLAttentionBackend.build_cross_mask(qs.to(device), ks.to(device), device)
    torch.testing.assert_close(cross.cpu(), qs[:, None] == ks[None, :])


def test_npu_real_rotary_matches_complex_reference():
    torch.manual_seed(1)
    rope = WanRotaryPosEmbed(128, (1, 2, 2), 1024).float()
    grid = torch.randint(0, 30, (1, 3, 10))
    x = torch.randn(1, 10, 2, 128).bfloat16()
    ref = _apply_rotary_emb(x, rope(grid)[:, :, None])
    actual = _apply_rotary_emb(
        x.npu(), rope.npu()(grid.npu())[:, :, None]
    ).cpu()
    torch.testing.assert_close(actual, ref, atol=0.02, rtol=0.02)
