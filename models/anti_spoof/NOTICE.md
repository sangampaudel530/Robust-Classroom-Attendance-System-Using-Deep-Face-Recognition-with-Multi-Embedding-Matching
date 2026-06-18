# Anti-spoofing models — attribution

The two ONNX files in this directory are the pretrained **Silent-Face-Anti-Spoofing**
MiniFASNet models from minivision-ai, converted from their original PyTorch
`.pth` weights to ONNX (no architecture or weight changes; conversion verified to
match the original outputs to ~1e-7).

- Source project: https://github.com/minivision-ai/Silent-Face-Anti-Spoofing
- Original weights: `2.7_80x80_MiniFASNetV2.pth`, `4_0_0_80x80_MiniFASNetV1SE.pth`
- License: Apache License 2.0 (see `LICENSE` in this directory)

These are an ensemble of two MiniFASNet models (input 80x80, BGR, raw [0,255]
range — the original `to_tensor` does **not** divide by 255). Each outputs 3-class
logits; we softmax each, sum across both models, and take the argmax. Class index
**1 = real/live**; indices 0 and 2 are spoof/attack classes. Each model uses a
specific context crop scale around the face bounding box (2.7 and 4.0
respectively), reproduced in `backend/services/anti_spoof.py`.
