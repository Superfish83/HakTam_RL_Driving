from argparse import Action
import tensorflow as tf
import tensorflow.python.keras as keras
from keras import optimizers
from tensorflow.python.keras.optimizer_v2.adam import Adam
import numpy as np
from keras.layers import Dense
from keras.layers import Flatten
from math import *

import csv

class ActionWeightLayer(tf.keras.layers.Layer):
  def __init__(self, num_outputs):
    super(ActionWeightLayer, self).__init__()
    self.num_outputs = num_outputs
    self.trainable = False #훈련을 해도 가중치가 바뀌지 않음

  def build(self, input_shape):
    self.kernel = self.add_weight("kernel",
                                  shape=[int(input_shape[-1]),
                                         self.num_outputs])

  def call(self, inputs):
    return tf.math.multiply(inputs, self.kernal)

class DuelingDQN(keras.Model):
    def __init__(self, n_actions, fc1Dims, fc2Dims, fc3Dims):
        super(DuelingDQN, self).__init__()
        self.flatten = Flatten()
        self.dense1 = Dense(fc1Dims, activation='relu')
        self.dense2 = Dense(fc2Dims, activation='relu')
        self.dense3 = Dense(fc3Dims, activation='relu')
        self.V = Dense(1, activation=None)
        self.A = Dense(n_actions, activation=None)
    

    def call(self, state):
        x = self.flatten(state)
        x = self.dense1(x)
        x = self.dense2(x)
        x = self.dense3(x)
        V = self.V(x)
        A = self.A(x)
        #A = self.Aw(A)
        
        Q = (V + (A - tf.reduce_mean(A, axis=1, keepdims=True)))
        return Q

    def advantage(self, state):
        x = self.flatten(state)
        x = self.dense1(x)
        x = self.dense2(x)
        x = self.dense3(x)
        A = self.A(x)
        #A = self.Aw(A)

        return A


class ReplayBuffer():
    def __init__(self, max_size, input_shape, per_on):
        self.per_on = per_on #Prioritized Experience Replay 사용 여부
        print('Use Prioritized Sampling:', self.per_on)

        self.mem_size = max_size
        self.mem_cntr = 0 #인덱스 지정을 위한 카운터
        self.mem_N = 0 #저장된 데이터 개수

        #NumPy 행렬을 이용한 메모리 구현
        self.state_memory = np.zeros((self.mem_size, *input_shape), dtype=np.float32)
        self.new_state_memory = np.zeros((self.mem_size, *input_shape), dtype=np.float32)
        self.action_memory = np.zeros(self.mem_size, dtype=np.int32)
        self.reward_memory = np.zeros(self.mem_size, dtype=np.float32)
        self.terminal_memory = np.zeros(self.mem_size, dtype=np.bool8)
        self.tderror_memory = np.zeros(self.mem_size, dtype=np.float32)
        self.blackbox_memory = np.zeros(self.mem_size, dtype=np.float32)


    def store_transition(self, state, action, reward, new_state, done, tderror):
        self.mem_cntr += 1
        
        index = self.mem_cntr % self.mem_size
        self.state_memory[index] = state
        self.new_state_memory[index] = new_state
        self.action_memory[index] = action
        self.reward_memory[index] = reward
        self.terminal_memory[index] = done
        self.tderror_memory[index] = tderror
        self.blackbox_memory[index] = 1e-7

        self.mem_N = max(index, self.mem_N)
    
    def set_blackbox(self, cnt):
        for i in range(cnt):
            idx = (self.mem_cntr - i) % self.mem_size
            self.blackbox_memory[idx] = 1.0
    
    def update_tderror(self, index, tderror):
        self.tderror_memory[index] = tderror

    def sample_buffer(self, batch_size, alpha, exp_no):

        # https://numpy.org/doc/stable/reference/random/generated/numpy.random.choice.htm
        if self.per_on: #Prioritized (Stochatic) Sampling
            # (1) Prioritization Based on TD-Error
            sample_scores = self.tderror_memory[:self.mem_N]
            sample_scores = np.power(sample_scores, alpha) + 0.01
                
            #sample_scores += 10.0 * alpha * self.blackbox_memory[:self.mem_N]

            sample_prob = sample_scores / np.sum(sample_scores)
            
            # (2) Prioritization Based on Reward
            #sample_scores = self.reward_memory[:self.mem_N]
            #print(sample_scores)
            #sample_scores = np.abs(sample_scores) + 1.0
            #sample_scores = np.power(sample_scores, alpha)
            #sample_prob = sample_scores / np.sum(sample_scores)
            
            batch = np.random.choice(self.mem_N, batch_size, replace=False, p=sample_prob)

            #debug
            #print(sample_scores[batch])

        else: #Random Sampling
            batch = np.random.choice(self.mem_N, batch_size, replace=False)

        states = self.state_memory[batch]
        new_states = self.new_state_memory[batch]
        actions = self.action_memory[batch]
        rewards = self.reward_memory[batch]
        dones = self.terminal_memory[batch]

        with open(f'learn_data_202211119({exp_no}).csv', 'a', encoding='utf-8', newline='') as f: # 샘플링된 데이터 기록
            wr = csv.writer(f)
            for i in batch[:10]:
                wr.writerow([alpha, sample_scores[i], self.tderror_memory[i], self.state_memory[i][0], self.action_memory[i], self.reward_memory[i]])

        return batch, states, actions, rewards, new_states, dones

class Agent(): #신경망 학습을 관장하는 클래스
    def __init__(self, lr, gamma, n_actions, epsilon, batch_size, input_dims, per_on,
        eps_dec = 1e-4, eps_end = 0.01, mem_size = 500000, fc1_dims=128,
        fc2_dims=128, fc3_dims=32, replace = 100):
        self.action_space = [i for i in range(n_actions)]
        self.gamma = gamma
        self.epsilon = epsilon
        self.eps_dec = eps_dec
        self.eps_end = eps_end
        self.replace = replace
        self.batch_size = batch_size

        self.learn_step_counter = 0
        self.memory = ReplayBuffer(mem_size, input_dims, per_on)
        self.episode_frame_cnt = 0 # BlackBox Prioritization을 위한 카운터

        # Double DQN
        self.q_eval = DuelingDQN(n_actions, fc1_dims, fc2_dims, fc3_dims)
        self.q_next = DuelingDQN(n_actions, fc1_dims, fc2_dims, fc3_dims)

        self.q_eval.compile(optimizer=Adam(learning_rate=lr), loss = "mse")
        self.q_next.compile(optimizer=Adam(learning_rate=lr), loss = "mse")

        self.init_q_next = True


    def store_transition(self, state, action, reward, new_state, done, pred):
        #TD-Error 계산
        target = reward + np.max(self.gamma*self.q_next(np.array([new_state]))*(1-int(done)))
        tderror = abs(target - pred)
        #print('TD-Error:', tderror)

        self.memory.store_transition(state, action, reward, new_state, done, tderror)
        self.episode_frame_cnt += 1
        
        if done:
            if reward != 0:
                #BlackBox Prioritization을 위한 점수 배정
                self.memory.set_blackbox(min(self.episode_frame_cnt, 10))
            self.episode_frame_cnt = 0
    
    def choose_action(self, observation):
        state = np.array([observation])
        
        if np.random.random() < self.epsilon:
            action = np.random.choice(self.action_space)
        else:
            actions = self.q_eval.advantage(state)
            action = tf.math.argmax(actions, axis=1).numpy()[0]

        pred = np.max(self.q_eval(state))
        return action, pred
    
    def learn(self, exp_no):
        if self.memory.mem_N < self.batch_size:
            return 0.0
        
        batch, states, actions, rewards, states_, dones = \
            self.memory.sample_buffer(self.batch_size, self.epsilon, exp_no)
            #alpha ~ 1.0 ~ 0.0

        if self.learn_step_counter % self.replace == 0:
            self.q_next.set_weights(self.q_eval.get_weights())
            #print("q_next weight set!")
        q_pred = self.q_eval(states)
        q_next = self.q_next(states_)
        q_target = q_pred.numpy()
        #print(q_next)

        #print(self.q_eval(np.array([[0,0,0,0,0,0]])))
        max_actions = tf.math.argmax(self.q_eval(states_), axis=1)

        for idx, terminal in enumerate(dones):
            action, pred = self.choose_action(states[idx])
            q_target[idx, actions[idx]] = rewards[idx] + \
                self.gamma*q_next[idx, max_actions[idx]]*(1-int(dones[idx]))

            memory_idx = batch[idx]
            tderror = abs(np.max(q_target[idx]) - pred)
            self.memory.update_tderror(memory_idx, tderror)
            #데이터 학습에 사용 후 저장된 TD-Error 값 업데이트

        loss = self.q_eval.train_on_batch(states, q_target) #그냥 학습

        #print(loss)
        # epsilon 조정은 훈련 코드에서 수동으로 하는 걸로 조정함. (20220920)
        #if self.epsilon > self.eps_end:
        #    self.epsilon -= self.eps_dec
        #else:
        #    self.epsilon = self.eps_end
        self.learn_step_counter += 1

        return loss

    def save_model(self, path):
        self.q_eval.save_weights(path)
        print("saved weights to " + path)
    
    def load_model(self, path):
        
        #self.q_eval(np.zeros([4,6]))
        #self.q_next(np.zeros([4,6]))
        print('test')

        self.q_eval.load_weights(path)
        self.q_next.load_weights(path)
        print("loaded weights from " + path)



#ActionWeightLayer 테스트

#Aw = ActionWeightLayer(5)
#Aw.setweight([0.017, 0.728, 0.0026, 0.119, 0.134])
#_ = np.array([1.0,1.0,1.0,1.0,1.0])
#__ = Aw(_)
#print(_)
#print(__)