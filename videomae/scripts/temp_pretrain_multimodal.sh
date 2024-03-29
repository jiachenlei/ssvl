OMP_NUM_THREADS=100 python -m torch.distributed.launch --nproc_per_node=1 \
        --master_port 12320 --nnodes=1 --node_rank=$1 --master_addr=$2 \
        ../run_mae_pretraining.py \
        --overwrite command-line \
        --config $3 \
        --project pretrain_multimodal_epic55 \
        --name temp \
        --debug
        # --wandb_id 193gzvg6
        # --debug
        # --wandb_id 3p64h4fm \
        # --resume /data/shared/output/multimodal_preepic55_A0/checkpoint-39.pth