# Usage: source /data5T/d00942093/zero-wam/activate.sh
source /usr/local/Ascend/ascend-toolkit/set_env.sh
source /data5T/d00942093/miniforge3/etc/profile.d/conda.sh
conda activate /data5T/d00942093/zero-wam/env
export ZERO_WAM_ROOT=/data5T/d00942093/zero-wam
export MODEL_PATH="$ZERO_WAM_ROOT/models/zero-wam-posttrain-robotwin"
export HF_HOME="$ZERO_WAM_ROOT/cache/huggingface"
export PIP_CACHE_DIR="$ZERO_WAM_ROOT/cache/pip"
export OMP_NUM_THREADS=8
export TOKENIZERS_PARALLELISM=false
