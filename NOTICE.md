# 第三方聲明

本專案的程式碼以 MIT 授權釋出（見 [LICENSE](LICENSE)）。以下是本專案使用或參考的第三方作品與它們的授權。

## 隨本專案散布

| 作品 | 用途 | 授權 |
| --- | --- | --- |
| [ML2021_ASR_ST](https://huggingface.co/datasets/ky552/ML2021_ASR_ST) test 分割的 12 句錄音與逐字稿 | 自動調校用的範例語音 `app/assets/tune-sample.ogg` 與參考逐字稿 `app/assets/tune-sample.json` | MIT，出處與引用方式見 [app/assets/NOTICE.md](app/assets/NOTICE.md) |

## 參考的程式碼

| 作品 | 用途 | 授權 |
| --- | --- | --- |
| [mtkresearch/Breeze-ASR-25](https://github.com/mtkresearch/Breeze-ASR-25) 的 [`run.py`](https://github.com/mtkresearch/Breeze-ASR-25/blob/main/run.py) | PyTorch 推論沿用其 transformers pipeline 設定（`chunk_length_s=0` 的逐段長音訊解碼） | MIT，Copyright (c) 2025 mtkresearch |

## 執行時下載（不隨本專案散布）

| 作品 | 用途 | 授權 |
| --- | --- | --- |
| [MediaTek-Research/Breeze-ASR-25](https://huggingface.co/MediaTek-Research/Breeze-ASR-25) | 語音辨識模型 | Apache-2.0 |
| [MediaTek-Research/Breeze-ASR-26](https://huggingface.co/MediaTek-Research/Breeze-ASR-26) | 語音辨識模型 | Apache-2.0 |
| [csukuangfj/sherpa-onnx-pyannote-segmentation-3-0](https://huggingface.co/csukuangfj/sherpa-onnx-pyannote-segmentation-3-0)（[pyannote segmentation 3.0](https://huggingface.co/pyannote/segmentation-3.0) 的 ONNX 版） | 說話者分段 | MIT |
| [csukuangfj/speaker-embedding-models](https://huggingface.co/csukuangfj/speaker-embedding-models) 的 [3D-Speaker](https://github.com/modelscope/3D-Speaker) ERes2Net（ONNX 版） | 聲紋 | 原始模型為 Apache-2.0；ONNX 轉檔的 repo 未附授權檔 |
| Python 套件（見 `pyproject.toml` 與 `uv.lock`） | 執行環境 | 各自的授權 |

`imageio-ffmpeg` 會在安裝時下載 ffmpeg 執行檔，ffmpeg 依編譯方式為 LGPL 或 GPL。原始碼 repo 不含 ffmpeg；若要散布包含它的 Docker 映像檔，需一併遵守 ffmpeg 的授權。

## 商標

Breeze ASR 是 MediaTek Research 的模型名稱。本專案是使用這些模型的非官方應用，與 MediaTek 無關，也未經其背書。
