# codes are from
# https://github.com/Tushar-N/pytorch-resnet3d/blob/master/models/resnet.py

# from matplotlib.cbook import flatten
import torch
import torch.nn as nn
import torch.nn.functional as F
import math


class SimpleCNN(nn.Module):

    def __init__(self, in_ch=3, tublet_size=2, patch_size=[16,16], num_classes=768):
        super().__init__()

        dim = num_classes // 8

        self.patch_embed = nn.Conv3d(in_ch, dim, kernel_size=(tublet_size, *patch_size), stride=(tublet_size, *patch_size), bias=False)

        self.conv1 = nn.Conv3d(dim, 2*dim, kernel_size=(3, 1, 1), stride=(1, 1, 1), padding=(1, 0, 0), bias=False)
        self.bn1 = nn.BatchNorm3d(2*dim)

        self.conv2 = nn.Conv3d(2*dim, 4*dim, kernel_size=(1, 3, 3), stride=(1, 1, 1), padding=(0, 1, 1), bias=False)
        self.bn2 = nn.BatchNorm3d(4*dim)

        self.conv3 = nn.Conv3d(4*dim, num_classes, kernel_size=1, stride=1, padding=0, bias=False)
        self.bn3 = nn.BatchNorm3d(num_classes)

        self.relu = nn.ReLU(inplace=True)

    def forward(self, x):

        x = self.patch_embed(x)
        # print(x.shape)
        x = self.conv1(x)
        x = self.bn1(x)
        x = self.relu(x)
        # print(x.shape)

        x = self.conv2(x)
        x = self.bn2(x)
        x = self.relu(x)
        # print(x.shape)

        x = self.conv3(x)
        x = self.bn3(x)
        x = self.relu(x)
        # print(x.shape)

        return x

class Tokenizer(nn.Module):
    """
        Edited by jiachen
        Feature extractor
    """
    def __init__(self, in_chans, feature_dim, tubelet_size, patch_size:list,  backbone="single"):
        super().__init__()
        self.backbone = backbone

        if backbone == "single":
            self.tokenizer =  nn.Conv3d(in_channels=in_chans, out_channels=feature_dim, 
                            kernel_size = (tubelet_size,  patch_size[0], patch_size[1]), 
                            stride=(tubelet_size,  patch_size[0],  patch_size[1]))

        elif backbone == "simplecnn":
            self.tokenizer = SimpleCNN(in_ch=in_chans, tublet_size=tubelet_size, patch_size=patch_size, num_classes=feature_dim)

        else:
            raise NotImplementedError(f"Unkown tokenizer backbone: {backbone}, expected to be one of [single, simplecnn]")

    def forward(self, x):
        """
        
            all_tokens: whether return all tokens or only return unmasked tokens
        """
        if self.backbone == "single":
            x = self.tokenizer(x).flatten(2).transpose(1, 2)
        elif self.backbone == "simplecnn":
            x = self.tokenizer(x).flatten(2).transpose(1, 2)
        else:
            raise NotImplementedError(f"Unkown tokenizer backbone: {self.backbone}, expected to be one of [single, simplecnn]")

        B, _, C = x.shape

        return x

class NonLocalBlock(nn.Module):
    def __init__(self, dim_in, dim_out, dim_inner):
        super(NonLocalBlock, self).__init__()

        self.dim_in = dim_in
        self.dim_inner = dim_inner  
        self.dim_out = dim_out

        self.theta = nn.Conv3d(dim_in, dim_inner, kernel_size=(1,1,1), stride=(1,1,1), padding=(0,0,0))
        self.maxpool = nn.MaxPool3d(kernel_size=(1,2,2), stride=(1,2,2), padding=(0,0,0))
        self.phi = nn.Conv3d(dim_in, dim_inner, kernel_size=(1,1,1), stride=(1,1,1), padding=(0,0,0))
        self.g = nn.Conv3d(dim_in, dim_inner, kernel_size=(1,1,1), stride=(1,1,1), padding=(0,0,0))

        self.out = nn.Conv3d(dim_inner, dim_out, kernel_size=(1,1,1), stride=(1,1,1), padding=(0,0,0))
        self.bn = nn.BatchNorm3d(dim_out)

    def forward(self, x):
        residual = x

        batch_size = x.shape[0]
        mp = self.maxpool(x)
        theta = self.theta(x)
        phi = self.phi(mp)
        g = self.g(mp)

        theta_shape_5d = theta.shape
        theta, phi, g = theta.view(batch_size, self.dim_inner, -1), phi.view(batch_size, self.dim_inner, -1), g.view(batch_size, self.dim_inner, -1)
      
        theta_phi = torch.bmm(theta.transpose(1, 2), phi) # (8, 1024, 784) * (8, 1024, 784) => (8, 784, 784)
        theta_phi_sc = theta_phi * (self.dim_inner**-.5)
        p = F.softmax(theta_phi_sc, dim=-1)

        t = torch.bmm(g, p.transpose(1, 2))
        t = t.view(theta_shape_5d)

        out = self.out(t)
        out = self.bn(out)

        out = out + residual
        return out


class Bottleneck(nn.Module):
    expansion = 4

    def __init__(self, inplanes, planes, stride, downsample, temp_conv, temp_stride, use_nl=False):
        super(Bottleneck, self).__init__()
        self.conv1 = nn.Conv3d(inplanes, planes, kernel_size=(1 + temp_conv * 2, 1, 1), stride=(temp_stride, 1, 1), padding=(temp_conv, 0, 0), bias=False)
        self.bn1 = nn.BatchNorm3d(planes)
        self.conv2 = nn.Conv3d(planes, planes, kernel_size=(1, 3, 3), stride=(1, stride, stride), padding=(0, 1, 1), bias=False)
        self.bn2 = nn.BatchNorm3d(planes)
        self.conv3 = nn.Conv3d(planes, planes * 4, kernel_size=1, stride=1, padding=0, bias=False)
        self.bn3 = nn.BatchNorm3d(planes * 4)
        self.relu = nn.ReLU(inplace=True)
        self.downsample = downsample
        self.stride = stride

        outplanes = planes * 4
        self.nl = NonLocalBlock(outplanes, outplanes, outplanes//2) if use_nl else None

    def forward(self, x):
        residual = x

        out = self.conv1(x)
        out = self.bn1(out)
        out = self.relu(out)

        out = self.conv2(out)
        out = self.bn2(out)
        out = self.relu(out)

        out = self.conv3(out)
        out = self.bn3(out)

        if self.downsample is not None:
            residual = self.downsample(x)

        out += residual
        out = self.relu(out)

        if self.nl is not None:
            out = self.nl(out)

        return out


class I3Res50(nn.Module):

    def __init__(self, in_ch, tublet_size=2, patch_size = [16, 16], block=Bottleneck, layers=[3, 4, 6, 3], num_classes=400, use_nl=False):

        self.inplanes = 64
        super(I3Res50, self).__init__()
        self.conv1 = nn.Conv3d(in_ch, 64, kernel_size=(tublet_size, *patch_size), stride=(tublet_size, *patch_size), bias=False)
        self.bn1 = nn.BatchNorm3d(64)
        self.relu = nn.ReLU(inplace=True)

        self.maxpool1 = nn.MaxPool3d(kernel_size=(2, 3, 3), stride=(2, 2, 2), padding=(0, 0, 0))
        self.maxpool2 = nn.MaxPool3d(kernel_size=(2, 1, 1), stride=(2, 1, 1), padding=(0, 0, 0))

        nonlocal_mod = 2 if use_nl else 1000
        self.layers = nn.Sequential(
                self._make_layer(block, 64, layers[0], stride=1, temp_conv=[1, 1, 1], temp_stride=[1, 1, 1]),
            )

        self.layer2 = self._make_layer(block, 128, layers[1], stride=2, temp_conv=[1, 0, 1, 0], temp_stride=[1, 1, 1, 1], nonlocal_mod=nonlocal_mod)
        self.layer3 = self._make_layer(block, 256, layers[2], stride=2, temp_conv=[1, 0, 1, 0, 1, 0], temp_stride=[1, 1, 1, 1, 1, 1], nonlocal_mod=nonlocal_mod)
        self.layer4 = self._make_layer(block, 512, layers[3], stride=2, temp_conv=[0, 1, 0], temp_stride=[1, 1, 1])
        self.avgpool = nn.AdaptiveAvgPool3d((1, 1, 1))
        self.fc = nn.Linear(512 * block.expansion, num_classes)
        self.drop = nn.Dropout(0.5)

        for m in self.modules():
            if isinstance(m, nn.Conv3d):
                m.weight = nn.init.kaiming_normal_(m.weight, mode='fan_out')
            elif isinstance(m, nn.BatchNorm3d):
                m.weight.data.fill_(1)
                m.bias.data.zero_()

    def _make_layer(self, block, planes, blocks, stride, temp_conv, temp_stride, nonlocal_mod=1000):
        downsample = None
        if stride != 1 or self.inplanes != planes * block.expansion or temp_stride[0]!=1:
            downsample = nn.Sequential(
                nn.Conv3d(self.inplanes, planes * block.expansion, kernel_size=(1, 1, 1), stride=(temp_stride[0], stride, stride), padding=(0, 0, 0), bias=False),
                nn.BatchNorm3d(planes * block.expansion)
                )

        layers = []
        layers.append(block(self.inplanes, planes, stride, downsample, temp_conv[0], temp_stride[0], False))
        self.inplanes = planes * block.expansion
        for i in range(1, blocks):
            layers.append(block(self.inplanes, planes, 1, None, temp_conv[i], temp_stride[i], i%nonlocal_mod==nonlocal_mod-1))

        return nn.Sequential(*layers)

    def forward(self, x):
        x = self.conv1(x)
        x = self.bn1(x)
        x = self.relu(x)
        x = self.maxpool1(x)

        x = self.layers(x)
        x = self.maxpool2(x)
        x = self.layer2(x)
        x = self.layer3(x)
        x = self.layer4(x)

        x = self.avgpool(x)
        x = self.drop(x)

        x = x.view(x.shape[0], -1)
        x = self.fc(x)
        return x


if __name__ == "__main__":
    model = SimpleCNN()

    x =  torch.randn(2, 3, 16, 224, 224)

    model(x)