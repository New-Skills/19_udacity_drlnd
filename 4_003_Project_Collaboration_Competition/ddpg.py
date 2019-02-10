import os
import numpy as np
import random
import copy
from collections import namedtuple, deque
from model import Actor, Critic

import torch
import torch.nn.functional as F
import torch.optim as optim

device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")


class DDGP(object):
    """Interacts with and learns from the environment."""

    def __init__(self, name, state_size, action_size, random_seed,
                 buffer_size=int(1e4), batch_size=512, min_req_exp=200, consec_learn_iter=2, learn_every=4,
                 lr_actor=1e-4, lr_critic=1e-3, weight_decay=1e-4, tau=1e-3, gamma=0.99):

        """Initialize an Agent object.

        Params
        ======
            state_size (int): dimension of each state
            action_size (int): dimension of each action
            random_seed (int): random seed

            buffer_size (int):  size of the "ReplauBuffer" pool
            batch_size (int): size of the batch used at each training epoch
            min_req_exp (int): minimum required episodes before starting to train
            consec_learn_iter (int): number of consecutive learning steps
            learn_every (int): number of episodes between learning steps

            lr_actor (float): actor's learning rate
            lr_critic (float): critic's learning rate
            weight_decay (float): L2 weight decay applied to the critic
            tau (float): soft update target parameter
            gamma (float): discount factor
        """

        # Agent parameters
        self.name = name
        self.state_size = state_size
        self.action_size = action_size
        self.seed = random.seed(random_seed)

        # Agent learning parameters
        self.buffer_size = buffer_size
        self.batch_size = batch_size
        self.min_req_exp = min_req_exp
        self.consec_learn_iter = consec_learn_iter
        self.learn_every = learn_every

        # Agent hyper-parameters

        self.lr_actor = lr_actor
        self.lr_critic = lr_critic
        self.weight_decay = weight_decay
        self.tau = tau
        self.gamma = gamma

        # Actor Network (w/ Target Network)
        self.actor_local = Actor(state_size, action_size, random_seed).to(device)
        self.actor_target = Actor(state_size, action_size, random_seed).to(device)
        self.actor_optimizer = optim.Adam(self.actor_local.parameters(), lr=self.lr_actor)

        # Critic Network (w/ Target Network)
        self.critic_local = Critic(state_size, action_size, random_seed).to(device)
        self.critic_target = Critic(state_size, action_size, random_seed).to(device)
        self.critic_optimizer = optim.Adam(self.critic_local.parameters(), lr=self.lr_critic,
                                           weight_decay=self.weight_decay)

        # Noise process
        self.noise = OUNoise(action_size, random_seed)

        # Replay memory
        self.memory = ReplayBuffer(self.buffer_size, random_seed)

        # Step memory
        self.step_mem = 0
        self.train = False

        # Exploration coefficient
        self.exploration = 1.0

    def memorize(self, state, action, reward, next_tate, done):
        """Inscribes in thmemory the experiences"""
        self.memory.add(state, action, reward, next_tate, done)

    def trigger_learn(self):
        self.train = False
        self.exploration *= 0.975

        if len(self.memory) > self.batch_size:
            self.learn()

    def update_counter(self):
        self.step_mem += 1

        if self.step_mem % self.learn_every == 0:
            self.train = True

    def act(self, state, add_noise=True):
        """Returns actions for given state as per current policy."""
        state = torch.from_numpy(state).float().to(device)

        self.actor_local.eval()
        with torch.no_grad():
            action = self.actor_local(state).cpu().data.numpy()
        self.actor_local.train()

        if add_noise:
            action += (self.noise.sample() * self.exploration)
        return np.clip(action, -1, 1)

    def reset(self):
        self.noise.reset()
        self.step_mem = 0
        self.train = False

    def learn(self):
        """Update policy and value parameters using given batch of experience tuples.
        Q_targets = r + γ * critic_target(next_state, actor_target(next_state))
        where:
            actor_target(state) -> action
            critic_target(state, action) -> Q-value

        Params
        ======
            experiences (Tuple[torch.Tensor]): tuple of (s, a, r, s', done) tuples
            gamma (float): discount factor
        """

        self.exploration *= 0.999

        experience = self.memory.sample()
        states, actions, rewards, next_states, dones = experience

        # TODO: arreglar
        state = states[(agent*BATCH_SIZE):((agent+1)*BATCH_SIZE)]
        action = actions[(agent*BATCH_SIZE):((agent+1)*BATCH_SIZE)]
        reward = rewards[:, agent]
        next_state = next_states[(agent*BATCH_SIZE):((agent+1)*BATCH_SIZE)]
        done = dones[:, agent]

        # ---------------------------- update critic ---------------------------- #
        # Get predicted next-state actions and Q values from target models
        actions_next = self.actor_target(next_state)
        Q_targets_next = self.critic_target(next_state, actions_next)
        # Compute Q targets for current states (y_i)
        Q_targets = reward + (GAMMA * Q_targets_next * (1 - done))
        # Compute critic loss
        Q_expected = self.critic_local(state, action)
        critic_loss = F.mse_loss(Q_expected, Q_targets)
        # Minimize the loss
        self.critic_optimizer.zero_grad()
        critic_loss.backward()
        torch.nn.utils.clip_grad_norm(self.critic_local.parameters(), 1)  # Gradient clipping
        self.critic_optimizer.step()

        # ---------------------------- update actor ---------------------------- #
        # Compute actor loss
        actions_pred = self.actor_local(state)
        actor_loss = -self.critic_local(state, actions_pred).mean()
        # Minimize the loss
        self.actor_optimizer.zero_grad()
        actor_loss.backward()
        torch.nn.utils.clip_grad_norm(self.actor_local.parameters(), 1)  # Gradient clipping
        self.actor_optimizer.step()

        # ----------------------- update target networks ----------------------- #
        self.soft_update(self.critic_local, self.critic_target, self.tau)
        self.soft_update(self.actor_local, self.actor_target, self.tau)

    def soft_update(self, local_model, target_model, tau):
        """Soft update model parameters.
        θ_target = τ*θ_local + (1 - τ)*θ_target

        Params
        ======
            local_model: PyTorch model (weights will be copied from)
            target_model: PyTorch model (weights will be copied to)
            tau (float): interpolation parameter
        """
        for target_param, local_param in zip(target_model.parameters(), local_model.parameters()):
            target_param.data.copy_(tau * local_param.data + (1.0 - tau) * target_param.data)

    def save(self, save_path, iteration):
        save_dict = {'actor_local_params': self.actor_local.state_dict(),
                     'actor_target_params': self.actor_target.state_dict(),
                     'actor_optim_params': self.actor_optimizer.state_dict(),
                     'critic_local_params': self.critic_local.state_dict(),
                     'critic_target_params': self.critic_target.state_dict(),
                     'critic_optim_params': self.critic_optimizer.state_dict()}

        torch.save(save_dict, os.path.join(save_path, self.name + 'i_episode-{}.pt'.format(iteration)))


class OUNoise:
    """Ornstein-Uhlenbeck process."""

    def __init__(self, size, seed, mu=0., theta=0.15, sigma=0.2):
        """Initialize parameters and noise process."""
        self.mu = mu * np.ones(size)
        self.theta = theta
        self.sigma = sigma
        self.seed = random.seed(seed)
        self.reset()

    def reset(self):
        """Reset the internal state (= noise) to mean (mu)."""
        self.state = copy.copy(self.mu)

    def sample(self):
        """Update internal state and return it as a noise sample."""
        x = self.state
        dx = self.theta * (self.mu - x) + self.sigma * np.array([random.random() for i in range(len(x))])
        self.state = x + dx
        return self.state


class ReplayBuffer:
    """Fixed-size buffer to store experience tuples."""

    def __init__(self, buffer_size, seed):
        """Initialize a ReplayBuffer object.
        Params
        ======
            buffer_size (int): maximum size of buffer
            batch_size (int): size of each training batch
        """
        self.memory = deque(maxlen=buffer_size)  # internal memory (deque)
        self.experience = namedtuple("Experience", field_names=["state", "action", "reward", "next_state", "done"])
        self.seed = random.seed(seed)

    def add(self, state, action, reward, next_state, done):
        """Add a new experience to memory."""
        e = self.experience(state, action, reward, next_state, done)
        self.memory.append(e)

    def sample(self):
        """Randomly sample a batch of experiences from memory."""
        experiences = random.sample(self.memory, k=self.batch_size)

        states = torch.from_numpy(np.vstack([e.state for e in experiences if e is not None])).float().to(device)
        actions = torch.from_numpy(np.vstack([e.action for e in experiences if e is not None])).float().to(device)
        rewards = torch.from_numpy(np.vstack([e.reward for e in experiences if e is not None])).float().to(device)
        next_states = torch.from_numpy(np.vstack([e.next_state for e in experiences if e is not None])).float().to(
            device)
        dones = torch.from_numpy(np.vstack([e.done for e in experiences if e is not None]).astype(np.uint8)).float().to(
            device)

        return states, actions, rewards, next_states, dones

    def __len__(self):
        """Return the current size of internal memory."""
        return len(self.memory)


def proces_samples(input_list):
    return torch.from_numpy(np.array(input_list))
