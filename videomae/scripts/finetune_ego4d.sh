OMP_NUM_THREADS=100 python -m torch.distributed.launch \
    --nproc_per_node=4 \
    --master_port 51225 --nnodes=1  --node_rank=$1 --master_addr=$2 \
    ../run_class_finetuning.py \
    --enable_deepspeed \
    --dist_eval \
    --config $3 \
    --overwrite command-line \
    --project oscc \
    --name  oscc_A6