OMP_NUM_THREADS=40 python -m torch.distributed.launch --nproc_per_node=1 \
        --master_port 12320 --nnodes=1 --node_rank=$1 --master_addr=$2 \
        ../run_mae_pretraining.py \
        --overwrite command-line \
        --config /data/shared/ssvl/videomae/config/temp/pretrain_flow_epic55.yml \
        --project pretrain_flow_epic55 \
        --name preepic55_A6 \
        --debug \
        --seed 413 \
 