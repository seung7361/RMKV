import torch

class TimeMixing(torch.nn.Module):
    def __init__(self, dim):
        super().__init__()
        self.dim = dim
        
        self.u = torch.nn.Parameter(torch.ones(1, 1, dim))
        self.w = torch.nn.Parameter(torch.ones(1, 1, dim))

        self.time_mix_receptance = torch.nn.Parameter(torch.ones(1, 1, dim))
        self.time_mix_key = torch.nn.Parameter(torch.ones(1, 1, dim))
        self.time_mix_value = torch.nn.Parameter(torch.ones(1, 1, dim))

        self.time_shift = torch.nn.ZeroPad2d((0, 0, 1, -1)) # shifts data one step to the right
        self.sigmoid = torch.nn.Sigmoid()

        self.key = torch.nn.Linear(dim, dim, bias=False)
        self.value = torch.nn.Linear(dim, dim, bias=False)
        self.receptance = torch.nn.Linear(dim, dim, bias=False)

        self.ln_out = torch.nn.Linear(dim, dim)
    

    def forward(self, x):
        B, T, d = x.shape
        x_shifted = self.time_shift(x)

        key = x * self.time_mix_key + x_shifted * (1 - self.time_mix_key)
        value = x * self.time_mix_value + x_shifted * (1 - self.time_mix_value)
        receptance = self.sigmoid(x * self.time_mix_receptance + x_shifted * (1 - self.time_mix_receptance))

        key, value, receptance = self.key(key), self.value(value), self.receptance(receptance)

        wkv = torch.zeros_like(key) # (B T d)
        a_t = torch.zeros_like(key[:, 0, :]) # (B d)
        b_t = torch.zeros_like(key[:, 0, :]) # (B d)

        for i in range(T):
            q = torch.maximum(self.u + key[:, i, :], self.w)

            a_t = torch.exp(-self.w - q) * a_t + torch.exp(self.u + key[:, i, :] - q) * value[:, i, :]
            b_t = torch.exp(-self.w - q) * b_t + torch.exp(self.u + key[:, i, :] - q)

            wkv[:, i, :] = a_t / b_t
        
        return self.ln_out(wkv * receptance)

class ChannelMixing(torch.nn.Module):
    def __init__(self, dim):
        super().__init__()
        self.dim = dim

        self.channel_mix_receptance = torch.nn.Parameter(torch.ones(1, 1, dim))
        self.channel_mix_key = torch.nn.Parameter(torch.ones(1, 1, dim))

        self.time_shift = torch.nn.ZeroPad2d((0, 0, 1, -1)) # shifts data one step to the right
        self.sigmoid = torch.nn.Sigmoid()

        self.key = torch.nn.Linear(dim, dim, bias=False)
        self.value = torch.nn.Linear(dim, dim, bias=False)
        self.receptance = torch.nn.Linear(dim, dim, bias=False)

        self.ln_out = torch.nn.Linear(dim, dim)
    
    def forward(self, x):
        B, T, d = x.shape
        x_shifted = self.time_shift(x)

        key = x * self.channel_mix_key + x_shifted * (1 - self.channel_mix_key)
        receptance = x * self.channel_mix_receptance + x_shifted * (1 - self.channel_mix_receptance)

        key = torch.square(torch.relu(self.key(key)))
        value = self.value(key)
        receptance = self.sigmoid(self.receptance(receptance))

        return receptance * value

class RWKVBlock(torch.nn.Module):
    def __init__(self, dim):
        super().__init__()
        self.dim = dim

        self.ln1 = torch.nn.LayerNorm(dim)
        self.ln2 = torch.nn.LayerNorm(dim)

        self.time_mixing = TimeMixing(dim)
        self.channel_mixing = ChannelMixing(dim)

        self.ln_out = torch.nn.LayerNorm(dim)
    
    def forward(self, x):
        attention = self.time_mixing(self.ln1(x))
        x = x + attention

        ffn = self.channel_mixing(self.ln2(x))
        x = x + ffn

        return x

class RWKVModel(torch.nn.Module):
    def __init__(self, vocab_size, dim, n_layers):
        super().__init__()
        self.dim = dim

        self.token_embedding = torch.nn.Embedding(vocab_size, dim)

        self.rwkv_blocks = torch.nn.ModuleList([
            RWKVBlock(dim) for _ in range(n_layers)
        ])

        self.ln_out = torch.nn.LayerNorm(dim)
        self.ln_in = torch.nn.LayerNorm(dim)
    
    def forward(self, x):
        B, T = x.shape
        x = self.token_embedding(x)

        x = self.ln_in(x)

        for rwkv_block in self.rwkv_blocks:
            x = rwkv_block(x)
        
        return self.ln_out(x)

class RWKVLMHeadModel(torch.nn.Module):
    def __init__(self, vocab_size, dim, n_layers):
        super().__init__()
        self.dim = dim

        self.model = RWKVModel(vocab_size, dim, n_layers)
        self.lm_head = torch.nn.Linear(dim, vocab_size, bias=False)
    
    def forward(self, x):
        x = self.model(x)
        return self.lm_head(x)
    
    # generation with top-k sampling
    def generate(self, x, k=10, p=0.9, max_len=100):
        x = x[:, None]
        for _ in range(max_len):
            out = self.forward(x)
            out = out[:, -1, :]
            out = torch.topk(out, k=k, dim=-1)[0]
            out = torch.multinomial(out, num_samples=1)
            x = torch.cat((x, out), dim=-1)
        return x