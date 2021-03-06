# -*- coding: utf-8 -*-`
import random
import numpy as np
import tensorflow as tf
import os

from agents.rl.utils.memory import ReplayMemory, LSTMemory
from agents.rl.utils.functions import softmax, argmax, general_advantage_estimates
from agents.rl.models.neural_network_models import LSTMNeuralNetworkModel

class A2CLSTM(object):
    def __init__(self,
                 num_state_params, 
                 num_actions,
                 timesteps = 5,
                 trainable=True,
                 
                 critic_lstm_units=[128],
                 critic_hidden_units=[128],
                 critic_activation_func='tanh', 
                 critic_kernel_initializer='glorot_uniform',
                 critic_learning_rate=0.0001,
                 critic_bacth_size=128,
                 
                 actor_lstm_units=[256],
                 actor_hidden_units=[256],
                 actor_activation_func='tanh', 
                 actor_kernel_initializer='glorot_uniform',  
                 actor_learning_rate=0.0001,
                 actor_bacth_size=2048,
                 
                 gamma=0.99, 
                 lam = 0.5,
                 
                 entropy_coef=0.9,
                 entropy_decoy=1,
                 max_entropy_part=0.9,
                 
                 max_grad_norm = 0,
                 
                 shared_memory = None
                 ):
        
        #Параметры игры
        self.num_actions = num_actions
        self.num_state_params = num_state_params
        self.timesteps = timesteps
        self.trainable = trainable
        
        #параметры обучения
        self.critic_optimizer = tf.keras.optimizers.Adam(critic_learning_rate, decay=0.99, epsilon=1e-5)
        self.actor_optimizer = tf.keras.optimizers.Adam(actor_learning_rate, decay=0.99, epsilon=1e-5)      
        self.max_grad_norm = max_grad_norm
        self.entropy_coef = entropy_coef
        self.entropy_decoy = entropy_decoy
        self.max_entropy_part = max_entropy_part
        self.gamma = gamma
        self.lam = lam
        self.train_step = 0
        self.critic_bacth_size = critic_bacth_size
        self.actor_bacth_size = actor_bacth_size
        
        # Step_model that is used for sampling
        if self.trainable:
            self._critic = LSTMNeuralNetworkModel(
                num_state_params,
                critic_lstm_units, 
                critic_hidden_units, 
                1,
                timesteps,
                activation_func=critic_activation_func, 
                kernel_initializer=critic_kernel_initializer,
                output_activation_func=critic_activation_func, 
                output_kernel_initializer=critic_kernel_initializer, )
        else:
            self._critic = None
        
        self._actor = LSTMNeuralNetworkModel(
            num_state_params, 
            actor_lstm_units, 
            actor_hidden_units, 
            num_actions,
            timesteps,
            activation_func=actor_activation_func, 
            kernel_initializer=actor_kernel_initializer,
            output_activation_func=actor_activation_func, 
            output_kernel_initializer=actor_kernel_initializer,)
        
        self.bug_fix()
        
        #memory
        if shared_memory is not None:
            self.memory = shared_memory
        else:   
            self.memory = ReplayMemory()
        
        #train memory
        self._mb_states = []
        self._mb_actions = []
        self._mb_returns = []
        self._mb_values = []
        
        #lstm
        self.lstm = LSTMemory(timesteps, (num_state_params,))
    
    def predict(self, inputs, training=False):
        
        policy_logits = self.predict_policy(inputs, training)
        values = self.predict_values(inputs, training)
        
        return policy_logits, values
    
    def predict_policy(self, inputs, training=False):
        
        policy_logits = self._actor(inputs)
        
        return policy_logits
    
    def predict_values(self, inputs, training=False):
        
        values = 0
        if self.trainable:
            values = self._critic(inputs)
            values = tf.reshape(values, [-1])
        
        return values
    
    def get_memory(self):
        return self.memory.get_samples()
        
    def feed_batch(self, batch):
        for i in range(len(batch['state'])):
            self.feed(batch['state'][i], 
                      batch['action'][i], 
                      batch['reward'][i], 
                      batch['next_state'][i], 
                      batch['done'][i])
        
    def feed(self, state, action, reward, next_state, done):
        replay = {
            'state': state,
            'action': action, 
            'reward': reward, 
            'next_state': next_state, 
            'done': done
            }
        self.memory.add_replay(replay)
        
    def clear_memory(self):
        self .memory.clear()
        
    def train(self):
        
        if self.trainable:
            samples = self.memory.get_samples()
            
            states = self.lstm.split_to_timesteps(samples['state'], samples['done'])
            est_values = self.predict_values(states)
            
            next_dones=np.copy(samples['done'])
            next_dones = np.roll(next_dones, -1)
            next_dones[-1] = True
            next_states = self.lstm.split_to_timesteps(samples['next_state'], next_dones)
            est_next_values = self.predict_values(next_states)
            
            #returns = self._returns_est(samples['reward'], samples['done'], est_values)
            #returns = self._returns(samples['reward'], samples['done'], est_values[-1])
            returns = general_advantage_estimates(
                samples['reward'],
                samples['done'], 
                est_values, 
                est_next_values, 
                self.lam,
                self.gamma)
            
            indices = [i for i in range(0, len(samples['state']))]
            random.shuffle(indices)
            
            states = np.asarray([states[i] for i in indices])
            actions = np.asarray([samples['action'][i] for i in indices])
            returns = np.asarray([returns[i] for i in indices])
            
            critic_loss = self._critic_train(states, returns)
            policy_loss, entropy_loss, policy_entropy_loss = self._actor_train(states, actions, returns)
    
            self.entropy_coef *= self.entropy_decoy
            self.train_step += 1
    
            test_state = np.asarray([states[0]])
            test_logit, test_value = self.predict(test_state)
            print("========================")
            print('train_step: ', self.train_step)
            print("------------------------")
            print('entropy coef: ', self.entropy_coef)
            print("------------------------")
            print('test logit: ', test_logit)
            print("------------------------")
            print('test value: ', test_value)
            print("------------------------")
            print('critic loss: ', critic_loss)
            print("------------------------")
            print('policy loss: ', policy_loss)
            print("------------------------")
            print('entropy loss: ', entropy_loss)
            print("------------------------")
            print('policy+entropy loss: ', policy_entropy_loss)
            print("========================")
            
            loss = [critic_loss, policy_loss, entropy_loss, policy_entropy_loss]
            self.clear_memory()
        
        return loss
    
    def _critic_train(self, states, returns):

        critic_loss_list = []

        for start in range(0, self.memory.size, self.critic_bacth_size):

            if self.memory.size > start+self.critic_bacth_size:

                indices = range(start, start+self.critic_bacth_size)
                self._mb_states=states[indices]
                self._mb_returns=returns[indices]
                
                critic_loss = self._critic_train_step()

                critic_loss_list.append(critic_loss.numpy())

        return critic_loss_list
    
    @tf.function
    def _critic_train_step(self):
        
        with tf.GradientTape() as tape:
                        
            values = self.predict_values(self._mb_states)
            value_loss = self._value_loss(self._mb_returns, values)
            
        value_weights = self._critic.trainable_weights
        value_gradients = tape.gradient(value_loss, value_weights)
        
        if self.max_grad_norm is not None:
            value_gradients, _ = tf.clip_by_global_norm(value_gradients, self.max_grad_norm)
        
        self.critic_optimizer.apply_gradients(zip(value_gradients, value_weights))
        
        return value_loss
    
    def _actor_train(self, states, actions, returns):

        entropy_loss_list = []
        policy_loss_list = []
        policy_entropy_loss_list = []
            
        values = self.predict_values(states).numpy()

        for start in range(0, self.memory.size, self.actor_bacth_size):

            if self.memory.size > start+self.actor_bacth_size:

                indices = range(start, start+self.actor_bacth_size)
                self._mb_states=states[indices]
                self._mb_actions=actions[indices]
                self._mb_returns=returns[indices]
                self._mb_values=values[indices]

                policy_loss, entropy_loss, policy_entropy_loss = self._actor_train_step()

                entropy_loss_list.append(entropy_loss.numpy())
                policy_loss_list.append(policy_loss.numpy())
                policy_entropy_loss_list.append(policy_entropy_loss.numpy())

        return policy_loss_list, entropy_loss_list, policy_entropy_loss_list 
    
    @tf.function
    def _actor_train_step(self):
        
        with tf.GradientTape() as tape:
                        
            policy_logits = self._actor(self._mb_states)
            advantages = self._advantages(self._mb_returns, self._mb_values)
            policy_loss = self._policy_loss(self._mb_actions, advantages, policy_logits)
            entropy_loss = self._entropy_loss(policy_logits)
            #clip_entropy_loss = tf.minimum(entropy_loss*self.max_entropy_part, entropy_loss)
            policy_entropy_loss = policy_loss - self.entropy_coef * entropy_loss
            
        policy_weights = self._actor.trainable_weights
        policy_gradients = tape.gradient(policy_entropy_loss, policy_weights)
        
        if self.max_grad_norm is not None:
            policy_gradients, _ = tf.clip_by_global_norm(policy_gradients, self.max_grad_norm)
            
        self.actor_optimizer.apply_gradients(zip(policy_gradients, policy_weights))
        return policy_loss, entropy_loss, policy_entropy_loss
    
    def get_weights(self):
        weights = {
            'critic': self._critic.get_weights(),
            'actor': self._actor.get_weights()
            }
        return weights
    
    def set_weights(self, weights):
        self._critic.set_weights(weights['critic'])
        self._actor.set_weights(weights['actor'])
        return weights
        
    def get_action(self, state, legal_actions):
        
        self.lstm.add_data(state)
        batch = [self.lstm.get_data()]
        ts = tf.convert_to_tensor(batch)
        
        logits = self.predict_policy(ts)
        probs = softmax(logits, legal_actions)[0]
        selected_action = np.random.choice(self.num_actions, p=probs)
        
        return selected_action
    
    def _value_loss(self, returns, values):
        
        value_loss = tf.math.reduce_mean(tf.keras.losses.MSE(returns, values))
        
        return value_loss
      
    def _policy_loss(self, actions, advantages, logits):
        
        actions = tf.cast(actions, dtype='int32')
        
        cross_entropy = tf.nn.sparse_softmax_cross_entropy_with_logits(
             labels=actions, 
             logits=logits)
        
        policy_loss = tf.math.reduce_mean(cross_entropy * advantages)
        
        return policy_loss
      
    def _entropy_loss(self, logits):
        
        entropy_loss = tf.math.reduce_mean(
            tf.keras.losses.categorical_crossentropy(logits, logits, from_logits=True))
        
        return entropy_loss
    
    def _advantages(self, returns, values):
        
        advantages = returns-values
        
        return advantages

    def bug_fix(self):
        shape = (1, self.timesteps, self.num_state_params)
        fix = np.random.random(shape)
        
        if self._critic is not None:
            self.predict_values(fix)
            self._critic.predict(fix)
        self.predict_policy(fix)
        self._actor.predict(fix)
        
    def reset_lstm_memory(self):
        self.lstm.reset()
        
    def save_model(self, path):

        if self.trainable:
            if not os.path.exists(path+'/critic'):
                os.makedirs(path+'/critic')
            self._critic.save(path+'/critic', save_format="tf")
                
        if not os.path.exists(path+'/actor'):
            os.makedirs(path+'/actor')
        self._actor.save(path+'/actor', save_format="tf")
        
    def load_model(self, path):
        if self.trainable:
            self._critic = tf.keras.models.load_model(path+'/critic')
        self._actor = tf.keras.models.load_model(path+'/actor')