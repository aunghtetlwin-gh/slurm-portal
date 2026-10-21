import os
import time
import torch

print("PID:", os.getpid())
print("PyTorch:", torch.__version__)
print("CUDA available:", torch.cuda.is_available())
print("GPU:", torch.cuda.get_device_name(0))

# Create a CUDA context and allocate a small tensor.
x = torch.ones(
    (2048, 2048),
    device="cuda"
)

# Perform a small GPU operation.
y = x * 2
torch.cuda.synchronize()

print("CUDA initialized.")
print("Sleeping for 60 seconds so we can inspect MPS...")

time.sleep(60)

print("Finished.")
