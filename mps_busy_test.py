import os
import sys
import time
import torch

name = sys.argv[1] if len(sys.argv) > 1 else "client"

print(f"{name} PID: {os.getpid()}")
print("GPU:", torch.cuda.get_device_name(0))
print(
    "MPS percentage:",
    os.getenv("CUDA_MPS_ACTIVE_THREAD_PERCENTAGE")
)

a = torch.randn(
    (2048, 2048),
    device="cuda"
)

b = torch.randn(
    (2048, 2048),
    device="cuda"
)

end_time = time.time() + 60
iterations = 0

while time.time() < end_time:
    c = torch.matmul(a, b)
    torch.cuda.synchronize()
    iterations += 1

print(
    f"{name} finished after {iterations} iterations"
)