"""This tutorial introduces Contractive auto-encoders (cA) using Theano.

 They are based on auto-encoders as the ones used in Bengio et
 al. 2007.  An autoencoder takes an input x and first maps it to a
 hidden representation y = f_{\theta}(x) = s(Wx+b), parameterized by
 \theta={W,b}. The resulting latent representation y is then mapped
 back to a "reconstructed" vector z \in [0,1]^d in input space z =
 g_{\theta'}(y) = s(W'y + b').  The weight matrix W' can optionally be
 constrained such that W' = W^T, in which case the autoencoder is said
 to have tied weights. The network is trained such that to minimize
 the reconstruction error (the error between x and z).  Adding the
 squared Frobenius norm of the Jacobian of the hidden mapping h with
 respect to the visible units yields the contractive auto-encoder:

      - \sum_{k=1}^d[ x_k \log z_k + (1-x_k) \log( 1-z_k)]  + \| \frac{\partial h(x)}{\partial x} \|^2

 References :
   - S. Rifai, P. Vincent, X. Muller, X. Glorot, Y. Bengio: Contractive
   Auto-Encoders: Explicit Invariance During Feature Extraction, ICML-11

   - S. Rifai, X. Muller, X. Glorot, G. Mesnil, Y. Bengio, and Pascal
     Vincent. Learning invariant features through local space
     contraction. Technical Report 1360, Universite de Montreal

   - Y. Bengio, P. Lamblin, D. Popovici, H. Larochelle: Greedy Layer-Wise
   Training of Deep Networks, Advances in Neural Information Processing
   Systems 19, 2007

"""

import cPickle
import gzip
import os
import sys
import time

import numpy

import theano
import theano.tensor as T
from theano.tensor.shared_randomstreams import RandomStreams

from logistic_sgd import load_data
from utils import tile_raster_images

import PIL.Image


class ContractiveAutoEncoder(object):
    """ Contractive Auto-Encoder class (cA)

    The contractive autoencoder tries to reconstruct the input with an
    additional constraint on the latent space. With the objective of
    obtaining a robust representation of the input space, we
    regularize the L2 norm(Froebenius) of the jacobian of the hidden
    representation with respect to the input. Please refer to Rifai et
    al.,2011 for more details.

    If x is the input then equation (1) computes the projection of the
    input into the latent space h. Equation (2) computes the jacobian
    of h with respect to x.  Equation (3) computes the reconstruction
    of the input, while equation (4) computes the reconstruction
    error and the added regularization term from Eq.(2).

    .. math::

        h_i = s(W_i x + b_i)                                             (1)

        J_i = h_i (1 - h_i) * W_i                                        (2)

        x' = s(W' h  + b')                                               (3)

        L = -sum_{k=1}^d [x_k \log x'_k + (1-x_k) \log( 1-x'_k)]
             + lambda * sum_{i=1}^d sum_{j=1}^n J_{ij}^2                 (4)

    """

    def __init__(self, numpy_rng, theano_rng=None, input=None,
                 n_visible=784, n_hidden=500,
                 W=None, bhid=None, bvis=None, learning_rate=0.1, contraction_level=0.1):
        """
        Initialize the dA class by specifying the number of visible units (the
        dimension d of the input ), the number of hidden units ( the dimension
        d' of the latent or hidden space ) and the corruption level. The
        constructor also receives symbolic variables for the input, weights and
        bias. Such a symbolic variables are useful when, for example the input
        is the result of some computations, or when weights are shared between
        the dA and an MLP layer. When dealing with SdAs this always happens,
        the dA on layer 2 gets as input the output of the dA on layer 1,
        and the weights of the dA are used in the second stage of training
        to construct an MLP.

        :type numpy_rng: numpy.random.RandomState
        :param numpy_rng: number random generator used to generate weights

        :type theano_rng: theano.tensor.shared_randomstreams.RandomStreams
        :param theano_rng: Theano random generator; if None is given one is
                     generated based on a seed drawn from `rng`

        :type input: theano.tensor.TensorType
        :param input: a symbolic description of the input or None for
                      standalone dA

        :type n_visible: int
        :param n_visible: number of visible units

        :type n_hidden: int
        :param n_hidden:  number of hidden units

        :type W: theano.tensor.TensorType
        :param W: Theano variable pointing to a set of weights that should be
                  shared belong the dA and another architecture; if dA should
                  be standalone set this to None

        :type bhid: theano.tensor.TensorType
        :param bhid: Theano variable pointing to a set of biases values (for
                     hidden units) that should be shared belong dA and another
                     architecture; if dA should be standalone set this to None

        :type bvis: theano.tensor.TensorType
        :param bvis: Theano variable pointing to a set of biases values (for
                     visible units) that should be shared belong dA and another
                     architecture; if dA should be standalone set this to None

        :type corruption_level: float
        :param corruption_level: The amount of input corruption to use. Should be between 0 and 1.
        """
        self.n_visible = n_visible
        self.n_hidden = n_hidden

        self.learning_rate = learning_rate
        self.contraction_level = contraction_level
        
        # create a Theano random generator that gives symbolic random values
        if not theano_rng:
            theano_rng = RandomStreams(numpy_rng.randint(2 ** 30))

        # note : W' was written as `W_prime` and b' as `b_prime`
        if not W:
            # W is initialized with `initial_W` which is uniformly sampled
            # from -4*sqrt(6./(n_visible+n_hidden)) and
            # 4*sqrt(6./(n_hidden+n_visible))the output of uniform if
            # converted using asarray to dtype
            # theano.config.floatX so that the code is runable on GPU
            initial_W = numpy.asarray(numpy_rng.uniform(
                      low=-4 * numpy.sqrt(6. / (n_hidden + n_visible)),
                      high=4 * numpy.sqrt(6. / (n_hidden + n_visible)),
                      size=(n_visible, n_hidden)), dtype=theano.config.floatX)
            W = theano.shared(value=initial_W, name='W', borrow=True)

        if not bvis:
            bvis = theano.shared(value=numpy.zeros(n_visible,
                                         dtype=theano.config.floatX),
                                 borrow=True)

        if not bhid:
            bhid = theano.shared(value=numpy.zeros(n_hidden,
                                                   dtype=theano.config.floatX),
                                 name='b',
                                 borrow=True)

        self.W = W
        # b corresponds to the bias of the hidden
        self.b = bhid
        # b_prime corresponds to the bias of the visible
        self.b_prime = bvis
        # tied weights, therefore W_prime is W transpose
        self.W_prime = self.W.T
        self.theano_rng = theano_rng
        # if no input is given, generate a variable representing the input
        if input == None:
            # we use a matrix because we expect a minibatch of several
            # examples, each example being a row
            self.x = T.matrix(name='input')
        else:
            self.x = input

        self.params = [self.W, self.b, self.b_prime]
        
        
        self.hidden = T.nnet.sigmoid(T.dot(self.x, self.W) + self.b)

        self.reconstructed = T.nnet.sigmoid(T.dot(self.hidden, self.W_prime) + self.b_prime)
        #self.reconstructed_L = - T.sum(self.x * T.log(self.reconstructed) + (1 - self.x) * T.log(1 - self.reconstructed), axis=1)
        self.reconstructed_L = T.sum((self.x - self.reconstructed)**2,axis=1)
        
        dummy = self.x - self.b_prime
        self.F = T.sum(T.nnet.softplus(self.hidden), axis=1) - 0.5*T.sum(dummy*dummy, axis=1)
        
    def get_hidden_values(self, input):
        """ Computes the values of the hidden layer """
        return T.nnet.sigmoid(T.dot(input, self.W) + self.b)

    def get_jacobian(self, hidden, W):
        """Computes the jacobian of the hidden layer with respect to
        the input, reshapes are necessary for broadcasting the
        element-wise product on the right axis

        """
        return T.reshape(hidden * (1 - hidden),
                         (self.batch_size, 1, self.n_hidden)) * T.reshape(
                             W, (1, self.n_visible, self.n_hidden))
        
    def get_reconstructed_input(self, hidden):
        """Computes the reconstructed input given the values of the
        hidden layer

        """
        return  T.nnet.sigmoid(T.dot(hidden, self.W_prime) + self.b_prime)

    def get_cost_updates(self, contraction_level, learning_rate):
        """ This function computes the cost and the updates for one training
        step of the cA """
        
        y = self.get_hidden_values(self.x)
        z = self.get_reconstructed_input(y)
        # note : we sum over the size of a datapoint; if we are using
        #        minibatches, L will be a vector, with one entry per
        #        example in minibatch
        #L = - T.sum(self.x * T.log(z) + (1 - self.x) * T.log(1 - z), axis=1)
        # (Traditional) Square error Loss
        L = T.sum((self.x - z)**2,axis=1)
        # note : L is now a vector, where each element is the
        #        cross-entropy cost of the reconstruction of the
        #        corresponding example of the minibatch. We need to
        #        compute the average of all these to get the cost of
        #        the minibatch
        J = self.get_jacobian(y, self.W)
        self.L_jacob = T.sum(J**2) / self.batch_size
        cost = T.mean(L) + contraction_level * T.mean(self.L_jacob)

        # compute the gradients of the cost of the `dA` with respect
        # to its parameters
        gparams = T.grad(cost, self.params)
        # generate the list of updates
        updates = []
        for param, gparam in zip(self.params, gparams):
            updates.append((param, param - learning_rate * gparam))

        return (cost, updates)

    def pred_reconstruction_L(self, input):
        y = self.get_hidden_values(self, input)
        z = self.get_reconstructed_input(y)
        L = T.sum((self.x - z)**2,axis=1)
        return L
    
    def fit(self, X, y=None, **kwargs):
        '''
        One-class algorithms need a fit stage
        '''     
        try:
            batch_size = kwargs['batch_size']
            self.batch_size = batch_size
        except KeyError:
            raise Exception("Need a batch size.")
        try:
            training_epochs = kwargs['training_epochs']
        except KeyError:
            raise Exception("How many training epochs?")
        #try:
            #self.contraction_level = kwargs['contraction_level']
        #except KeyError:
        #    raise Exception("How many training epochs?")
        # Load X into shared variable
        train_set_x = theano.shared(numpy.asarray(X,
                                                  dtype=theano.config.floatX),
                                    borrow=True)
        
        n_train_batches = train_set_x.get_value(borrow=True).shape[0] / batch_size
        #self.n_batch_size = n_train_batches
        
        # allocate symbolic variables for the data
        index = T.lscalar()    # index to a [mini]batch
        x = T.matrix('x')

        #####################################
        # BUILDING THE MODEL                #
        #####################################

        #da = dA(numpy_rng=rng, theano_rng=theano_rng, input=x,
        #        n_visible=27, n_hidden=100)

        cost, updates = self.get_cost_updates(contraction_level=self.contraction_level,
                                              learning_rate=self.learning_rate)

        train_da = theano.function([index], T.mean(self.reconstructed_L), updates=updates,
             givens={self.x: train_set_x[index * batch_size:
                                      (index + 1) * batch_size]})


        ############
        # TRAINING #
        ############
        #import pdb;pdb.set_trace()
        for epoch in xrange(training_epochs):
            c = []
            for batch_index in xrange(n_train_batches):
                c.append(train_da(batch_index))
            print 'Training epoch %d, cost ' % epoch, numpy.mean(c)

        #import pdb; pdb.set_trace()
        return self
    def predict(self, X):
        '''
        Predict anomaly scores for dataset X
        '''
        """
        This demo is tested on ALOI

        :type learning_rate: float
        :param learning_rate: learning rate used for training the DeNosing
                              AutoEncoder

        :type training_epochs: int
        :param training_epochs: number of epochs used for training

        :type dataset: string
        :param dataset: path to the picked dataset

        """
        pred_set_x = theano.shared(numpy.asarray(X,
                                                 dtype=theano.config.floatX),
                                   borrow=True)
        
        confidence_predict = theano.function(inputs=[],
            outputs = self.F,
            givens={self.x:pred_set_x}, on_unused_input='warn')

        return confidence_predict()
    

if __name__ == '__main__':
    # the autoencoder difficulty (loss) and y
    #confidences_per_dim, autoenc_confidence, autoenc_difficulty, y, da = test_dA_ALOI(training_epochs=50, batch_size=20)
    try:
        from sklearn.metrics import roc_auc_score
    except ImportError:
        from sklearn.metrics import auc_score as roc_auc_score
    #print roc_auc_score(y, autoenc_difficulty)
    #print roc_auc_score(y, -autoenc_confidence)
    #theano.config.compute_test_value = 'warn'
    import pandas as pd
    train_set = pd.read_csv('aloi-27d-50000-max5-tot1508.csv', sep=' ', header=None)
    X = train_set.ix[:, 0:26].values
    y = (train_set[29] == 'Outlier').values
    
    rng = numpy.random.RandomState(123)
    theano_rng = RandomStreams(rng.randint(2 ** 30))
    ca = ContractiveAutoEncoder(rng, theano_rng, n_visible=27, n_hidden=100).fit(X, batch_size=20, training_epochs=2)
    #import pdb; pdb.set_trace()
    autoenc_confidence = ca.predict(X)
    print roc_auc_score(y, autoenc_confidence)
    print "OK"