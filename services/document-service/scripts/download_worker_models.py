"""Build-time ONLY: download official pretrained models; no user data."""
import contextlib
import os
from pathlib import Path
import shutil

os.environ["PADDLE_PDX_MODEL_SOURCE"]="BOS"
os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"]="True"
root=Path("/opt/document-ocr")
models={"det":"PP-OCRv5_mobile_det","rec":"latin_PP-OCRv5_mobile_rec"}
with open(os.devnull,"w") as quiet,contextlib.redirect_stdout(quiet):
    from paddleocr import PaddleOCR
    PaddleOCR(text_detection_model_name=models["det"],text_recognition_model_name=models["rec"],device="cpu",
        enable_mkldnn=False,cpu_threads=2,use_doc_orientation_classify=False,use_doc_unwarping=False,use_textline_orientation=False)
for folder,name in models.items():
    source=Path.home()/".paddlex"/"official_models"/name
    if not (source/"inference.yml").is_file(): raise RuntimeError("Model download incomplete")
    shutil.copytree(source,root/folder,dirs_exist_ok=True)
print("PASS: pinned PP-OCRv5 detector and Latin recognizer cached for offline inference.")
