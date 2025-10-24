import time

def run_pipeline_adapter(input_text: str) -> str:
    """
    Minimal fake pipeline:
    - Pretend to process something (sleep 1–2s)
    - Return a transformed string (uppercased)
    Replace this with your real captioning/audio pipeline later.
    """
    time.sleep(1.5)
    return f"DEMO OUTPUT:\n{input_text.strip().upper()}"
