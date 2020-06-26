import torch
from torch import nn, optim
from torch.utils import data
from torchnet import meter
from tqdm import tqdm
import numpy as np
import os
from datetime import datetime

from loggers import TensorboardLogger
from utils.device import move_to, detach


class Trainer():
    def __init__(self, device,
                 config,
                 model,
                 criterion,
                 optimizer,
                 scheduler,
                 metric):
        super(Trainer, self).__init__()

        self.config = config
        self.device = device
        self.model = model
        self.criterion = criterion
        self.optimizer = optimizer
        self.scheduler = scheduler
        self.metric = metric

        # Train ID
        self.train_id = self.config.get('id', 'None')
        self.train_id += '-' + datetime.now().strftime('%Y_%m_%d-%H_%M_%S')

        # Get arguments
        self.nepochs = self.config['trainer']['nepochs']
        self.log_step = self.config['trainer']['log_step']
        self.val_step = self.config['trainer']['val_step']
        self.debug = None #self.config['debug']

        # Instantiate global variables
        self.best_loss = np.inf
        self.best_metric = 0.0
        self.val_loss = list()
        self.val_metric = list()

        # Instantiate loggers
        self.save_dir = os.path.join('runs', self.train_id)
        self.tsboard = TensorboardLogger(path=self.save_dir)

    def save_checkpoint(self, epoch, val_loss, val_metric):

        data = {
            'epoch': epoch,
            'model_state_dict': self.model.state_dict(),
            'optimizer_state_dict': self.optimizer.state_dict(),
            'config': self.config
        }

        if val_loss < self.best_loss:
            print(
                f'Loss is improved from {self.best_loss: .6f} to {val_loss: .6f}. Saving weights...')
            torch.save(data, os.path.join(self.save_dir, 'best_loss.pth'))
            # Update best_loss
            self.best_loss = val_loss
        else:
            print(f'Loss is not improved from {self.best_loss:.6f}.')

        if val_metric > self.best_metric:
            print("Metric is improved from %.5f to %.5f. Saving weights ...." %  (self.best_metric, val_metric))
            torch.save(data, os.path.join(self.save_dir, "best_metric.pth")) 
            # Update best_loss
            self.best_metric = val_metric
        
        else:
            print("Metric is not improved from %.5f." % (self.best_metric))

        print('Saving current model...')
        torch.save(data, os.path.join(self.save_dir, 'current.pth'))

    def train_epoch(self, epoch, dataloader):
        # 0: Record loss during training process
        running_loss = meter.AverageValueMeter()
        total_loss = meter.AverageValueMeter()
        self.metric.reset()
        self.model.train()
        print('Training........')
        progress_bar = tqdm(dataloader)
        for i, (inp, lbl) in enumerate(progress_bar):
            # 1: Load img_inputs and labels
            inp = move_to(inp, self.device)
            lbl = move_to(lbl, self.device)
            # 2: Clear gradients from previous iteration
            self.optimizer.zero_grad()
            # 3: Get network outputs
            outs = self.model(inp)
            # 4: Calculate the loss
            loss = self.criterion(outs, lbl)
            # 5: Calculate gradients
            loss.backward()
            # 6: Performing backpropagation
            self.optimizer.step()
            with torch.no_grad():
                # 7: Update loss
                running_loss.add(loss.item())
                total_loss.add(loss.item())

                if (i + 1) % self.log_step == 0 or (i + 1) == len(dataloader):
                    self.tsboard.update_loss(
                        'train', running_loss.value()[0], epoch * len(dataloader) + i)
                    running_loss.reset()

                # 8: Update metric
                outs = detach(outs)
                lbl = detach(lbl)
                # for m in self.metric.values():
                value = self.metric.calculate(outs, lbl)
                self.metric.update(value)

        print('+ Training result')
        avg_loss = total_loss.value()[0]
        print('Loss:', avg_loss)
        self.metric.summary()
    @torch.no_grad()
    def val_epoch(self, epoch, dataloader):
        running_loss = meter.AverageValueMeter()
        self.metric.reset()

        self.model.eval()
        print('Evaluating........')
        progress_bar = tqdm(dataloader)
        for i, (inp, lbl) in enumerate(progress_bar):
            # 1: Load inputs and labels
            inp = move_to(inp, self.device)
            lbl = move_to(lbl, self.device)
            # 2: Get network outputs
            outs = self.model(inp)
            # 3: Calculate the loss
            loss = self.criterion(outs, lbl)
            # 4: Update loss
            running_loss.add(loss.item())
            # 5: Update metric
            outs = detach(outs)
            lbl = detach(lbl)
            value = self.metric.calculate(outs, lbl)
            self.metric.update(value)

        print('+ Evaluation result')
        avg_loss = running_loss.value()[0]
        print('Loss:', avg_loss)
        self.val_loss.append(avg_loss)
        self.val_metric.append(self.metric.value())
        self.tsboard.update_loss('val', avg_loss, epoch)

        print(self.metric.summary()) 
        self.tsboard.update_metric('val', 'val', self.metric.value(), epoch)

    def train(self, train_dataloader, val_dataloader):
        for epoch in range(self.nepochs):
            print('\nEpoch {:>3d}'.format(epoch))
            print('-----------------------------------')

            # 1: Training phase
            self.train_epoch(epoch=epoch, dataloader=train_dataloader)

            print()

            # 2: Evalutation phase
            if (epoch + 1) % self.val_step == 0:
                # 2: Evaluating model
                self.val_epoch(epoch, dataloader=val_dataloader)
                print('-----------------------------------')

                # 3: Learning rate scheduling
                self.scheduler.step(self.val_loss[-1])

                # 4: Saving checkpoints
                if not self.debug:
                    # Get latest val loss here
                    val_loss = self.val_loss[-1]
                    val_metric = self.val_metric[-1]
                    self.save_checkpoint(epoch, val_loss, val_metric)
