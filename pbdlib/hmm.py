from termcolor import colored
import numpy as np

from pbdlib.functions import *
from pbdlib.model import *
from pbdlib.gmm import *

import math
from numpy.linalg import inv, pinv, norm, det
import sys


class HMM(GMM):
	def __init__(self, nb_states, nb_dim=2):
		GMM.__init__(self, nb_states, nb_dim)

		self._trans = None
		self._init_priors = None

	@property
	def init_priors(self):
		if self._init_priors is None:
			print colored("HMM init priors not defined, initializing to uniform", 'red', 'on_white')
			self._init_priors = np.ones(self.nb_states) / self.nb_states

		return self._init_priors

	@init_priors.setter
	def init_priors(self, value):
		self._init_priors = value

	@property
	def trans(self):
		if self._trans is None:
			print colored("HMM transition matrix not defined, initializing to uniform", 'red', 'on_white')
			self._trans = np.ones((self.nb_states, self.nb_states)) / self.nb_states
		return self._trans

	@trans.setter
	def trans(self, value):
		self._trans = value

	@property
	def Trans(self):
		return self.trans

	@Trans.setter
	def Trans(self, value):
		self.trans = value

	def viterbi(self, demo):
		"""
		Compute most likely sequence of state given observations

		:param demo: 	[np.array([nb_timestep, nb_dim])]
		:return:
		"""

		nb_data, dim = demo.shape if isinstance(demo, np.ndarray) else demo['x'].shape

		logB = np.zeros((self.nb_states, nb_data))
		logDELTA = np.zeros((self.nb_states, nb_data))
		PSI = np.zeros((self.nb_states, nb_data)).astype(int)

		_, logB = self.obs_likelihood(demo)

		# forward pass
		logDELTA[:, 0] = np.log(self.init_priors + realmin) + logB[:, 0]

		for t in range(1, nb_data):
			for i in range(self.nb_states):
				# get index of maximum value : most probables
				PSI[i, t] = np.argmax(logDELTA[:, t - 1] + np.log(self.Trans[:, i] + realmin))
				logDELTA[i, t] = np.max(logDELTA[:, t - 1] + np.log(self.Trans[:, i] + realmin)) + logB[i, t]

		# backtracking
		q = [0 for i in range(nb_data)]
		q[-1] = np.argmax(logDELTA[:, -1])
		for t in range(nb_data - 2, -1, -1):
			q[t] = PSI[q[t + 1], t + 1]

		return q

	def obs_likelihood(self, demo=None, dep=None, marginal=None, sample_size=200, demo_idx=None):
		sample_size = demo.shape[0]
		# emission probabilities
		B = np.ones((self.nb_states, sample_size))

		if marginal != []:
			for i in range(self.nb_states):
				mu, sigma = (self.mu, self.sigma)

				if marginal is not None:
					mu, sigma = self.get_marginal(marginal)

				if dep is None :
					B[i, :] = multi_variate_normal(demo,
												   mu[i],
												   sigma[i], log=True)
				else:  # block diagonal computation
					B[i, :] = 1.0
					for d in dep:
						dGrid = np.ix_([i], d, d)
						B[[i], :] += multi_variate_normal(demo, mu[d, i],
														  sigma[dGrid][0], log=True)

		return np.exp(B), B

	def compute_messages(self, demo=None, dep=None, table=None, marginal=None, sample_size=200, demo_idx=None):
		"""

		:param demo: 	[np.array([nb_timestep, nb_dim])]
		:param dep: 	[A x [B x [int]]] A list of list of dimensions
			Each list of dimensions indicates a dependence of variables in the covariance matrix
			E.g. [[0],[1],[2]] indicates a diagonal covariance matrix
			E.g. [[0, 1], [2]] indicates a full covariance matrix between [0, 1] and no
			covariance with dim [2]
		:param table: 	np.array([nb_states, nb_demos]) - composed of 0 and 1
			A mask that avoid some demos to be assigned to some states
		:param marginal: [slice(dim_start, dim_end)] or []
			If not None, compute messages with marginals probabilities
			If [] compute messages without observations, use size
			(can be used for time-series regression)
		:return:
		"""
		if isinstance(demo, np.ndarray):
			sample_size = demo.shape[0]
		elif isinstance(demo, dict):
			sample_size = demo['x'].shape[0]

		B, _ = self.obs_likelihood(demo, dep, marginal, sample_size)
		# if table is not None:
		# 	B *= table[:, [n]]

		self._B = B

		# forward variable alpha (rescaled)
		alpha = np.zeros((self.nb_states, sample_size))
		alpha[:, 0] = self.init_priors * B[:, 0]
		c = np.zeros(sample_size)
		c[0] = 1.0 / np.sum(alpha[:, 0] + realmin)
		alpha[:, 0] = alpha[:, 0] * c[0]

		for t in range(1, sample_size):
			alpha[:, t] = alpha[:, t - 1].dot(self.Trans) * B[:, t]
			# Scaling to avoid underflow issues
			c[t] = 1.0 / np.sum(alpha[:, t] + realmin)
			alpha[:, t] = alpha[:, t] * c[t]

		# backward variable beta (rescaled)
		beta = np.zeros((self.nb_states, sample_size))
		beta[:, -1] = np.ones(self.nb_states) * c[-1]  # Rescaling
		for t in range(sample_size - 2, -1, -1):
			beta[:, t] = np.dot(self.Trans, beta[:, t + 1]) * B[:, t + 1]
			beta[:, t] = np.minimum(beta[:, t] * c[t], realmax)

		# Smooth node marginals, gamma
		gamma = (alpha * beta) / np.tile(np.sum(alpha * beta, axis=0) + realmin,
										 (self.nb_states, 1))

		# Smooth edge marginals. zeta (fast version, considers the scaling factor)
		zeta = np.zeros((self.nb_states, self.nb_states, sample_size - 1))

		for i in range(self.nb_states):
			for j in range(self.nb_states):
				zeta[i, j, :] = self.Trans[i, j] * alpha[i, 0:-1] * B[j, 1:] * beta[
																			   j,
																			   1:]

		return alpha, beta, gamma, zeta, c

	def gmm_init(self, data, **kwargs):
		if isinstance(data, list):
			data = np.concatenate(data, axis=0)
		GMM.em(self, data, **kwargs)

		self.init_priors = np.ones(self.nb_states) / self.nb_states
		self.Trans = np.ones((self.nb_states, self.nb_states))/self.nb_states

	def em(self, demos, dep=None, reg=1e-8, table=None, end_cov=False, cov_type='full', dep_mask=None):
		"""

		:param demos:	[list of np.array([nb_timestep, nb_dim])]
				or [lisf of dict({})]
		:param dep:		[A x [B x [int]]] A list of list of dimensions
			Each list of dimensions indicates a dependence of variables in the covariance matrix
			E.g. [[0],[1],[2]] indicates a diagonal covariance matrix
			E.g. [[0, 1], [2]] indicates a full covariance matrix between [0, 1] and no
			covariance with dim [2]
		:param reg:		[float] or list [nb_dim x float] for different regularization in different dimensions
			Regularization term used in M-step for covariance matrices
		:param table:		np.array([nb_states, nb_demos]) - composed of 0 and 1
			A mask that avoid some demos to be assigned to some states
		:param end_cov:	[bool]
			If True, compute covariance matrix without regularization after convergence
		:param cov_type: 	[string] in ['full', 'diag', 'spherical']
		:return:
		"""
		nb_min_steps = 5  # min num iterations
		nb_max_steps = 50  # max iterations
		max_diff_ll = 1e-4  # max log-likelihood increase

		nb_samples = len(demos)
		data = np.concatenate(demos).T
		nb_data = data.shape[0]

		s = [{} for d in demos]
		# stored log-likelihood
		LL = np.zeros(nb_max_steps)

		self.reg = reg
		# create regularization matrix

		for it in range(nb_max_steps):

			for n, demo in enumerate(demos):
				s[n]['alpha'], s[n]['beta'], s[n]['gamma'], s[n]['zeta'], s[n]['c'] = HMM.compute_messages(self, demo, dep, table)

			# concatenate intermediary vars
			gamma = np.hstack([s[i]['gamma'] for i in range(nb_samples)])
			zeta = np.dstack([s[i]['zeta'] for i in range(nb_samples)])
			gamma_init = np.hstack([s[i]['gamma'][:, 0:1] for i in range(nb_samples)])
			gamma_trk = np.hstack([s[i]['gamma'][:, 0:-1] for i in range(nb_samples)])

			gamma2 = gamma / (np.sum(gamma, axis=1, keepdims=True) + realmin)

			# M-step
			for i in range(self.nb_states):
				# Update centers
				self.mu[i] = np.einsum('a,ia->i',gamma2[i], data)

				# Update covariances
				Data_tmp = data - self.mu[i][:, None]
				self.sigma[i] = np.einsum('ij,jk->ik',
												np.einsum('ij,j->ij', Data_tmp,
														  gamma2[i, :]), Data_tmp.T)
				# Regularization
				self.sigma[i] = self.sigma[i] + self.reg

				if cov_type == 'diag':
					self.sigma[i] *= np.eye(self.sigma.shape[1])

			if dep_mask is not None:
				self.sigma *= dep_mask

			# Update initial state probablility vector
			self.init_priors = np.mean(gamma_init, axis=1)

			# Update transition probabilities
			self.Trans = np.sum(zeta, axis=2) / (np.sum(gamma_trk, axis=1) + realmin)
			# print self.Trans
			# Compute avarage log-likelihood using alpha scaling factors
			LL[it] = 0
			for n in range(nb_samples):
				LL[it] -= sum(np.log(s[n]['c']))
			LL[it] = LL[it] / nb_samples

			self._gammas = [s_['gamma'] for s_ in s]

			# Check for convergence
			if it > nb_min_steps:
				if LL[it] - LL[it - 1] < max_diff_ll:
					if end_cov:
						for i in range(self.nb_states):
							# recompute covariances without regularization
							Data_tmp = data - self.Mu[:, [i]]
							self.Sigma[:, :, i] = np.einsum('ij,jk->ik',
												np.einsum('ij,j->ij', Data_tmp,
														  gamma2[i, :]), Data_tmp.T)

						if cov_type == 'diag':
							self.sigma[i] *= np.eye(self.sigma.shape[1])

					# print "EM converged after " + str(it) + " iterations"
					# print LL[it]
					return gamma


		print "EM did not converge"
		print LL
		return gamma

	def score(self, demos):
		"""

		:param demos:	[list of np.array([nb_timestep, nb_dim])]
		:return:
		"""
		ll = []
		for n, demo in enumerate(demos):
			_, _, _, _, c = self.compute_messages(demo)
			ll += [np.sum(np.log(c))]

		return ll

	def condition(self, data_in, dim_in, dim_out, h=None, gmm=False):
		if gmm:
			return super(HMM, self).condition(data_in, dim_in, dim_out)
		else:
			a, _, _, _, _ = self.compute_messages(data_in, marginal=dim_in)

			return super(HMM, self).condition(data_in, dim_in, dim_out, h=a)

	"""
	To ensure compatibility
	"""
	@property
	def Trans(self):
		return self.trans

	@Trans.setter
	def Trans(self, value):
		self.trans = value