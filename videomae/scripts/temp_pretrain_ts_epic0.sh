OMP_NUM_THREADS=40 python -m torch.distributed.launch --nproc_per_node=2 \
        --master_port 12320 --nnodes=4 --node_rank=$1 --master_addr=$2 \
        ../run_mae_pretraining.py \
        --overwrite command-line \
        --config $3 \
        --project pretrain_ts_epic55 \
        --name ts_preepic55_A36 \
        # --debug

# /data/shared/ssvl/videomae/config/temp/pretrain_ts_epic55.yml
 