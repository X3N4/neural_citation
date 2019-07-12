import math
import time
import random
import logging
from pathlib import Path
from datetime import datetime
from tqdm import tqdm
from typing import Tuple

import torch
from torch import nn
from torch import optim
import torch.nn.functional as F
import torch.nn.init as init
from torch.utils.tensorboard import SummaryWriter
from torchtext.data import BucketIterator

import core
from core import DEVICE, SEED, PathOrStr
from data_utils import generate_bucketized_iterators
from model import NeuralCitationNetwork

logger = logging.getLogger("neural_citation.train")


def init_weights(m):
    """
    Initializes the model layers. Convolutional layers use he-uniform initialization,
    linear layers use xavier-uniform.  
    
    ## Parameters:  
    
    - **m** *(nn.Module)*: Layer of the network.   
    """
    if isinstance(m, nn.Conv2d):
        init.kaiming_uniform_(m.weight, a=0, nonlinearity="relu")
    elif isinstance(m, nn.Linear):
        init.xavier_uniform_(m.weight)


def epoch_time(start_time: float, end_time: float) -> Tuple[int, int]:
    """
    Measures the time elapsed between two time stamps.  
    
    ## Parameters:  
    
    - **start_time** *(float)*: Starting time stamp.  
    - **end_time** *(float)*: Ending time stamp.  
    """
    elapsed_time = end_time - start_time
    elapsed_mins = int(elapsed_time / 60)
    elapsed_secs = int(elapsed_time - (elapsed_mins * 60))
    return elapsed_mins, elapsed_secs


# TODO: Document this
def train(model, iterator, optimizer, criterion, clip):
    
    model.train()
    
    epoch_loss = 0
    
    for i, batch in enumerate(iterator):
        
        # unpack and move to GPU if available
        cntxt, citing, ttl, cited = batch.context, batch.authors_citing, batch.title_cited, batch.authors_cited
        cntxt = cntxt.to(DEVICE)
        citing = citing.to(DEVICE)
        ttl = ttl.to(DEVICE)
        cited = cited.to(DEVICE)
        
        optimizer.zero_grad()
        
        output = model(context = cntxt, title = ttl, authors_citing = citing, authors_cited = cited)
        
        #trg = [trg sent len, batch size]
        #output = [trg sent len, batch size, output dim]
        
        output = output[1:].view(-1, output.shape[-1])
        ttl = ttl[1:].view(-1)
        
        #trg = [(trg sent len - 1) * batch size]
        #output = [(trg sent len - 1) * batch size, output dim]
        
        loss = criterion(output, ttl)
        
        loss.backward()
        
        torch.nn.utils.clip_grad_norm_(model.parameters(), clip)
        
        optimizer.step()
        
        epoch_loss += loss.item()
        
    return epoch_loss / len(iterator)


# TODO: Document this
def evaluate(model, iterator, criterion):
    
    model.eval()
    
    epoch_loss = 0
    
    with torch.no_grad():
    
        for i, batch in enumerate(iterator):

            # unpack and move to GPU if available
            cntxt, citing, ttl, cited = batch.context, batch.authors_citing, batch.title_cited, batch.authors_cited
            cntxt = cntxt.to(DEVICE)
            citing = citing.to(DEVICE)
            ttl = ttl.to(DEVICE)
            cited = cited.to(DEVICE)
            
            output = model(context = cntxt, title = ttl, authors_citing = citing, authors_cited = cited)

            #trg = [trg sent len, batch size]
            #output = [trg sent len, batch size, output dim]

            output = output[1:].view(-1, output.shape[-1])
            ttl = ttl[1:].view(-1)

            #trg = [(trg sent len - 1) * batch size]
            #output = [(trg sent len - 1) * batch size, output dim]

            loss = criterion(output, ttl)

            epoch_loss += loss.item()
        
    return epoch_loss / len(iterator)


def train_ncn(model: nn.Module, train_iterator: BucketIterator, valid_iterator: BucketIterator, 
              n_epochs: int = 10, clip: int = 5, 
              save_dir: PathOrStr = "./models") -> None:
    
    optimizer = optim.Adam(model.parameters(), lr=0.01)
    criterion = nn.CrossEntropyLoss(ignore_index = PAD_IDX, reduction="sum")

    best_valid_loss = float('inf')

    # set up tensorboard and data logging
    date = datetime.now()
    log_dir = Path(f"runs/{date.year}_NCN_{date.month}_{date.day}_{date.hour}")
    writer = SummaryWriter(log_dir=log_dir)

    for epoch in range(n_epochs):
        
        start_time = time.time()
        
        train_loss = train(model, train_iterator, optimizer, criterion, clip)
        valid_loss = evaluate(model, valid_iterator, criterion)

        end_time = time.time()

        epoch_mins, epoch_secs = epoch_time(start_time, end_time)#

        writer.add_scalar('loss/training', train_loss)
        writer.add_scalar('loss/validation', valid_loss)
        
        if valid_loss < best_valid_loss:
            best_valid_loss = valid_loss
            if not save_dir.exists(): save_dir.mkdir()
            torch.save(model.state_dict(), save_dir/f"NCN_{date.month}_{date.day}_{date.hour}.pt")
        
        print(f'Epoch: {epoch+1:02} | Time: {epoch_mins}m {epoch_secs}s')
        print(f'\tTrain Loss: {train_loss:.3f} | Train PPL: {math.exp(train_loss):7.3f}')
        print(f'\t Val. Loss: {valid_loss:.3f} |  Val. PPL: {math.exp(valid_loss):7.3f}')


if __name__ == '__main__':
    # Set the random seeds before training
    random.seed(SEED)
    torch.manual_seed(SEED)
    torch.backends.cudnn.deterministic = True

    # set up training
    data = generate_bucketized_iterators("/home/timo/DataSets/KD_arxiv_CS/arxiv_data.csv")
    PAD_IDX = data.ttl.vocab.stoi['<pad>']
    cntxt_vocab_len = len(data.cntxt.vocab)
    aut_vocab_len = len(data.aut.vocab)
    ttl_vocab_len = len(data.ttl.vocab)
    

    net = NeuralCitationNetwork(context_filters=[4,4,5], context_vocab_size=cntxt_vocab_len,
                                authors=True, author_filters=[1,2], author_vocab_size=aut_vocab_len,
                                title_vocab_size=ttl_vocab_len, pad_idx=PAD_IDX, num_layers=2)
    net.to(DEVICE)
    net.apply(init_weights)

    train_ncn(net, data.train_iter, data.valid_iter)
