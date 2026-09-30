# Usage: source script/ascend/activate.sh
export ZERO_WAM_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
source /usr/local/Ascend/ascend-toolkit/set_env.sh
source "${ZERO_WAM_CONDA_ROOT:-$(dirname -- "$ZERO_WAM_ROOT")/../miniforge3}/etc/profile.d/conda.sh"
conda activate "${ZERO_WAM_ENV:-$(dirname -- "$ZERO_WAM_ROOT")/env}"
export MODEL_PATH="$ZERO_WAM_ROOT/models/zero-wam-posttrain-robotwin"
export HF_HOME="$(dirname -- "$ZERO_WAM_ROOT")/cache/huggingface"
export PIP_CACHE_DIR="$(dirname -- "$ZERO_WAM_ROOT")/cache/pip"
export OMP_NUM_THREADS=8
export TOKENIZERS_PARALLELISM=false
