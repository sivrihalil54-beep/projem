# Dataset istatistiği (Python)
./venv/bin/python -c "from utils.captcha_dataset_collector import dataset_stats; print(dataset_stats())"

# Eğitim hazır mı?
./venv/bin/python utils/captcha_model_trainer.py --check-only

# step1 + model testi (model yoksa bir test skip olur)
./venv/bin/python -m pytest tests/test_captcha_model_accuracy.py -q