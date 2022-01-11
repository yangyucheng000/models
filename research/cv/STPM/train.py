# Copyright 2022 Huawei Technologies Co., Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ============================================================================
"""train"""
import os
import argparse
import ast
import mindspore.nn as nn
from mindspore import context
from mindspore.train import Model
from mindspore.train.serialization import load_checkpoint, load_param_into_net
from mindspore.train.callback import CheckpointConfig
from mindspore.train.callback import ModelCheckpoint
from mindspore.train.callback import TimeMonitor, LossMonitor

from src.dataset import createDataset
from src.STPM import STPM
from src.loss import MyLoss
from src.EvalOneStep import EvalOneStepCell
from src.callbacks import EvalCallBack

parser = argparse.ArgumentParser(description='train')

parser.add_argument('--train_url', type=str)
parser.add_argument('--data_url', type=str)
parser.add_argument('--modelarts', type=ast.literal_eval, default=False, help="using modelarts")

parser.add_argument('--category', type=str, default='screw')
parser.add_argument('--device_id', type=int, default=0, help='Device id')
parser.add_argument('--pre_ckpt_path', type=str, help='Pretrain checkpoint file path')
parser.add_argument('--epoch', type=int, default=100, help="epoch size")
parser.add_argument('--lr', type=float, default=0.4, help="learning rate")

args = parser.parse_args()

if args.modelarts:
    import moxing as mox


class MyWithLossCell(nn.Cell):
    """define loss network"""

    def __init__(self, backbone, loss_fn):
        super(MyWithLossCell, self).__init__(auto_prefix=False)
        self.backbone = backbone
        self.loss_fn = loss_fn

    def construct(self, img, gt, label, idx):
        fs, ft = self.backbone(img)
        return self.loss_fn(fs, ft)


def train():
    """train"""
    context.set_context(mode=context.GRAPH_MODE,
                        device_target='Ascend',
                        save_graphs=False)
    if args.modelarts:
        device_id = int(os.getenv('DEVICE_ID'))
        context.set_context(device_id=device_id)
    else:
        context.set_context(device_id=args.device_id)

    if args.modelarts:
        mox.file.copy_parallel(src_url=args.data_url, dst_url='/cache/dataset/')
        train_dataset_path = '/cache/dataset/'

        ds_train, ds_test = createDataset(train_dataset_path, args.category)
        if not os.path.exists("cache/train_output"):
            os.makedirs("cache/train_output")
    else:
        ds_train, ds_test = createDataset(args.data_url, args.category)

    # network
    net = STPM()
    net.model_s.set_train(True)
    param = load_checkpoint(args.pre_ckpt_path)
    load_param_into_net(net, param)
    for p in net.model_t.trainable_params():
        p.requires_grad = False

    # loss
    loss_func = MyLoss()
    epochs = args.epoch
    step = ds_train.get_dataset_size()

    lr = args.lr
    opt = nn.SGD(net.model_s.trainable_params(), learning_rate=lr, momentum=0.9, weight_decay=0.0001, nesterov=True)
    net_with_criterion = MyWithLossCell(net, loss_func)
    train_net = nn.TrainOneStepCell(net_with_criterion, opt)

    model = Model(train_net)
    eval_network = EvalOneStepCell(net)
    eval_cb = EvalCallBack(ds_test, eval_network, args.category, end_epoch=epochs)
    time_cb = TimeMonitor()
    loss_cb = LossMonitor()
    ckpt_config = CheckpointConfig(save_checkpoint_steps=step, keep_checkpoint_max=1)
    if args.modelarts:
        check_cb = ModelCheckpoint(prefix=args.category, directory='/cache/train_output', config=ckpt_config)
    else:
        check_cb = ModelCheckpoint(prefix=args.category, directory='./ckpt', config=ckpt_config)
    cb = [check_cb, time_cb, loss_cb, eval_cb]
    model.train(epoch=epochs, train_dataset=ds_train, callbacks=cb, dataset_sink_mode=True)

    if args.modelarts:
        mox.file.copy_parallel(src_url='/cache/train_output', dst_url=args.train_url)


if __name__ == '__main__':
    train()
