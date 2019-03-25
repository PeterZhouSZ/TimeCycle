from __future__ import print_function, division
import os
import sys
from skimage import io
import pandas as pd
import numpy as np
import torch
from torch.nn.modules.module import Module
from torch.utils.data import Dataset
from torch.autograd import Variable
import torch.nn.functional as F
from utils.torch_util import expand_dim

class GeometricTnf(object):
    """

    Geometric transfromation to an image batch (wrapped in a PyTorch Variable)
    ( can be used with no transformation to perform bilinear resizing )

    """
    def __init__(self, geometric_model='affine', tps_grid_size=3, tps_reg_factor=0, out_h=240, out_w=240, offset_factor=None, use_cuda=True):
        self.out_h = out_h
        self.out_w = out_w
        self.geometric_model = geometric_model
        self.use_cuda = use_cuda
        self.offset_factor = offset_factor

        if geometric_model=='affine' and offset_factor is None:
            self.gridGen = AffineGridGen(out_h=out_h, out_w=out_w, use_cuda=use_cuda)
        elif geometric_model=='affine' and offset_factor is not None:
            self.gridGen = AffineGridGenV2(out_h=out_h, out_w=out_w, use_cuda=use_cuda)
        elif geometric_model=='tps':
            self.gridGen = TpsGridGen(out_h=out_h, out_w=out_w, grid_size=tps_grid_size,
                                      reg_factor=tps_reg_factor, use_cuda=use_cuda)
        if offset_factor is not None:
            self.gridGen.grid_X=self.gridGen.grid_X/offset_factor
            self.gridGen.grid_Y=self.gridGen.grid_Y/offset_factor

        self.theta_identity = torch.Tensor(np.expand_dims(np.array([[1,0,0],[0,1,0]]),0).astype(np.float32))
        if use_cuda:
            self.theta_identity = self.theta_identity.cuda()

    def __call__(self, image_batch, theta_batch=None, out_h=None, out_w=None, return_warped_image=True, return_sampling_grid=False, padding_factor=1.0, crop_factor=1.0):
        if image_batch is None:
            b=1
        else:
            b=image_batch.size(0)
        if theta_batch is None:
            theta_batch = self.theta_identity
            theta_batch = theta_batch.expand(b,2,3).contiguous()
            theta_batch = Variable(theta_batch,requires_grad=False)

        # check if output dimensions have been specified at call time and have changed
        if (out_h is not None and out_w is not None) and (out_h!=self.out_h or out_w!=self.out_w):
            if self.geometric_model=='affine':
                gridGen = AffineGridGen(out_h, out_w)
            elif self.geometric_model=='tps':
                gridGen = TpsGridGen(out_h, out_w, use_cuda=self.use_cuda)
        else:
            gridGen = self.gridGen

        sampling_grid = gridGen(theta_batch)

        # rescale grid according to crop_factor and padding_factor
        if padding_factor != 1 or crop_factor !=1:
            sampling_grid = sampling_grid*(padding_factor*crop_factor)
        # rescale grid according to offset_factor
        if self.offset_factor is not None:
            sampling_grid = sampling_grid*self.offset_factor

        if return_sampling_grid and not return_warped_image:
            return sampling_grid

        # sample transformed image
        warped_image_batch = F.grid_sample(image_batch, sampling_grid)

        if return_sampling_grid and return_warped_image:
            return (warped_image_batch,sampling_grid)

        return warped_image_batch



class GeometricTnfAffine(object):
    """

    Geometric transfromation to an image batch (wrapped in a PyTorch Variable)
    ( can be used with no transformation to perform bilinear resizing )

    """
    def __init__(self, geometric_model='affine', tps_grid_size=3, tps_reg_factor=0, out_h=240, out_w=240, offset_factor=None):
        self.out_h = out_h
        self.out_w = out_w
        self.geometric_model = geometric_model
        self.offset_factor = offset_factor

        self.gridGen = AffineGridGenV3(out_h=out_h, out_w=out_w)

        if offset_factor is not None:
            self.gridGen.grid_X=self.gridGen.grid_X/offset_factor
            self.gridGen.grid_Y=self.gridGen.grid_Y/offset_factor

    def __call__(self, image_batch, theta_batch=None, out_h=None, out_w=None, return_warped_image=True, return_sampling_grid=False, padding_factor=1.0, crop_factor=1.0):

        sampling_grid = self.gridGen(theta_batch)

        # rescale grid according to crop_factor and padding_factor
        if padding_factor != 1 or crop_factor !=1:
            sampling_grid = sampling_grid*(padding_factor*crop_factor)
        # rescale grid according to offset_factor
        if self.offset_factor is not None:
            sampling_grid = sampling_grid*self.offset_factor

        if return_sampling_grid and not return_warped_image:
            return sampling_grid

        # sample transformed image
        warped_image_batch = F.grid_sample(image_batch, sampling_grid)

        if return_sampling_grid and return_warped_image:
            return (warped_image_batch,sampling_grid)

        return warped_image_batch




class AffineGridGen(Module):
    def __init__(self, out_h=240, out_w=240, out_ch = 3, use_cuda=True):
        super(AffineGridGen, self).__init__()
        self.out_h = out_h
        self.out_w = out_w
        self.out_ch = out_ch

    def forward(self, theta):
        b=theta.size()[0]
        if not theta.size()==(b,2,3):
            theta = theta.view(-1,2,3)
        theta = theta.contiguous()
        batch_size = theta.size()[0]
        out_size = torch.Size((batch_size,self.out_ch,self.out_h,self.out_w))
        return F.affine_grid(theta, out_size)

class AffineGridGenV2(Module):
    def __init__(self, out_h=240, out_w=240, use_cuda=True):
        super(AffineGridGenV2, self).__init__()
        self.out_h, self.out_w = out_h, out_w
        self.use_cuda = use_cuda

        # create grid in numpy
        # self.grid = np.zeros( [self.out_h, self.out_w, 3], dtype=np.float32)
        # sampling grid with dim-0 coords (Y)
        self.grid_X,self.grid_Y = np.meshgrid(np.linspace(-1,1,out_w),np.linspace(-1,1,out_h))
        # grid_X,grid_Y: size [1,H,W,1,1]
        self.grid_X = torch.FloatTensor(self.grid_X).unsqueeze(0).unsqueeze(3)
        self.grid_Y = torch.FloatTensor(self.grid_Y).unsqueeze(0).unsqueeze(3)
        self.grid_X = Variable(self.grid_X,requires_grad=False)
        self.grid_Y = Variable(self.grid_Y,requires_grad=False)
        if use_cuda:
            self.grid_X = self.grid_X.cuda()
            self.grid_Y = self.grid_Y.cuda()

    def forward(self, theta):
        b=theta.size(0)
        if not theta.size()==(b,6):
            theta = theta.view(b,6)
            theta = theta.contiguous()

        t0=theta[:,0].unsqueeze(1).unsqueeze(2).unsqueeze(3)
        t1=theta[:,1].unsqueeze(1).unsqueeze(2).unsqueeze(3)
        t2=theta[:,2].unsqueeze(1).unsqueeze(2).unsqueeze(3)
        t3=theta[:,3].unsqueeze(1).unsqueeze(2).unsqueeze(3)
        t4=theta[:,4].unsqueeze(1).unsqueeze(2).unsqueeze(3)
        t5=theta[:,5].unsqueeze(1).unsqueeze(2).unsqueeze(3)
        X = expand_dim(self.grid_X,0,b)
        Y = expand_dim(self.grid_Y,0,b)
        Xp = X*t0 + Y*t1 + t2
        Yp = X*t3 + Y*t4 + t5

        return torch.cat((Xp,Yp),3)


class AffineGridGenV3(Module):
    def __init__(self, out_h=240, out_w=240):
        super(AffineGridGenV3, self).__init__()
        self.out_h, self.out_w = out_h, out_w

        # create grid in numpy
        # self.grid = np.zeros( [self.out_h, self.out_w, 3], dtype=np.float32)
        # sampling grid with dim-0 coords (Y)
        self.grid_X,self.grid_Y = np.meshgrid(np.linspace(-1,1,out_w),np.linspace(-1,1,out_h))
        # grid_X,grid_Y: size [1,H,W,1,1]
        # self.grid_X = torch.FloatTensor(self.grid_X).unsqueeze(0).unsqueeze(3)
        # self.grid_Y = torch.FloatTensor(self.grid_Y).unsqueeze(0).unsqueeze(3)
        # self.grid_X = Variable(self.grid_X,requires_grad=False)
        # self.grid_Y = Variable(self.grid_Y,requires_grad=False)
        # if use_cuda:
        #     self.grid_X = self.grid_X.cuda()
        #     self.grid_Y = self.grid_Y.cuda()

    def forward(self, theta):
        b=theta.size(0)
        if not theta.size()==(b,6):
            theta = theta.view(b,6)
            theta = theta.contiguous()

        grid_X = torch.Tensor(self.grid_X).unsqueeze(0).unsqueeze(3).cuda()
        grid_Y = torch.Tensor(self.grid_Y).unsqueeze(0).unsqueeze(3).cuda()
        grid_X = Variable(grid_X,requires_grad=False)
        grid_Y = Variable(grid_Y,requires_grad=False)

        t0=theta[:,0].unsqueeze(1).unsqueeze(2).unsqueeze(3)
        t1=theta[:,1].unsqueeze(1).unsqueeze(2).unsqueeze(3)
        t2=theta[:,2].unsqueeze(1).unsqueeze(2).unsqueeze(3)
        t3=theta[:,3].unsqueeze(1).unsqueeze(2).unsqueeze(3)
        t4=theta[:,4].unsqueeze(1).unsqueeze(2).unsqueeze(3)
        t5=theta[:,5].unsqueeze(1).unsqueeze(2).unsqueeze(3)
        X = expand_dim(grid_X,0,b)
        Y = expand_dim(grid_Y,0,b)
        Xp = X*t0 + Y*t1 + t2
        Yp = X*t3 + Y*t4 + t5

        return torch.cat((Xp,Yp),3)





class TpsGridGen(Module):
    def __init__(self, out_h=240, out_w=240, use_regular_grid=True, grid_size=3, reg_factor=0, use_cuda=True):
        super(TpsGridGen, self).__init__()
        self.out_h, self.out_w = out_h, out_w
        self.reg_factor = reg_factor
        self.use_cuda = use_cuda

        # create grid in numpy
        # self.grid = np.zeros( [self.out_h, self.out_w, 3], dtype=np.float32)
        # sampling grid with dim-0 coords (Y)
        self.grid_X,self.grid_Y = np.meshgrid(np.linspace(-1,1,out_w),np.linspace(-1,1,out_h))
        # grid_X,grid_Y: size [1,H,W,1,1]
        self.grid_X = torch.FloatTensor(self.grid_X).unsqueeze(0).unsqueeze(3)
        self.grid_Y = torch.FloatTensor(self.grid_Y).unsqueeze(0).unsqueeze(3)
        self.grid_X = Variable(self.grid_X,requires_grad=False)
        self.grid_Y = Variable(self.grid_Y,requires_grad=False)
        if use_cuda:
            self.grid_X = self.grid_X.cuda()
            self.grid_Y = self.grid_Y.cuda()

        # initialize regular grid for control points P_i
        if use_regular_grid:
            axis_coords = np.linspace(-1,1,grid_size)
            self.N = grid_size*grid_size
            P_Y,P_X = np.meshgrid(axis_coords,axis_coords)
            P_X = np.reshape(P_X,(-1,1)) # size (N,1)
            P_Y = np.reshape(P_Y,(-1,1)) # size (N,1)
            P_X = torch.FloatTensor(P_X)
            P_Y = torch.FloatTensor(P_Y)
            self.Li = Variable(self.compute_L_inverse(P_X,P_Y).unsqueeze(0),requires_grad=False)
            self.P_X = P_X.unsqueeze(2).unsqueeze(3).unsqueeze(4).transpose(0,4)
            self.P_Y = P_Y.unsqueeze(2).unsqueeze(3).unsqueeze(4).transpose(0,4)
            self.P_X = Variable(self.P_X,requires_grad=False)
            self.P_Y = Variable(self.P_Y,requires_grad=False)
            if use_cuda:
                self.P_X = self.P_X.cuda()
                self.P_Y = self.P_Y.cuda()


    def forward(self, theta):
        warped_grid = self.apply_transformation(theta,torch.cat((self.grid_X,self.grid_Y),3))

        return warped_grid

    def compute_L_inverse(self,X,Y):
        N = X.size()[0] # num of points (along dim 0)
        # construct matrix K
        Xmat = X.expand(N,N)
        Ymat = Y.expand(N,N)
        P_dist_squared = torch.pow(Xmat-Xmat.transpose(0,1),2)+torch.pow(Ymat-Ymat.transpose(0,1),2)
        P_dist_squared[P_dist_squared==0]=1 # make diagonal 1 to avoid NaN in log computation
        K = torch.mul(P_dist_squared,torch.log(P_dist_squared))
        if self.reg_factor != 0:
            K+=torch.eye(K.size(0),K.size(1))*self.reg_factor
        # construct matrix L
        O = torch.FloatTensor(N,1).fill_(1)
        Z = torch.FloatTensor(3,3).fill_(0)
        P = torch.cat((O,X,Y),1)
        L = torch.cat((torch.cat((K,P),1),torch.cat((P.transpose(0,1),Z),1)),0)
        Li = torch.inverse(L)
        if self.use_cuda:
            Li = Li.cuda()
        return Li

    def apply_transformation(self,theta,points):
        if theta.dim()==2:
            theta = theta.unsqueeze(2).unsqueeze(3)
        # points should be in the [B,H,W,2] format,
        # where points[:,:,:,0] are the X coords
        # and points[:,:,:,1] are the Y coords

        # input are the corresponding control points P_i
        batch_size = theta.size()[0]
        # split theta into point coordinates
        Q_X=theta[:,:self.N,:,:].squeeze(3)
        Q_Y=theta[:,self.N:,:,:].squeeze(3)

        # get spatial dimensions of points
        points_b = points.size()[0]
        points_h = points.size()[1]
        points_w = points.size()[2]

        # repeat pre-defined control points along spatial dimensions of points to be transformed
        P_X = self.P_X.expand((1,points_h,points_w,1,self.N))
        P_Y = self.P_Y.expand((1,points_h,points_w,1,self.N))

        # compute weigths for non-linear part
        W_X = torch.bmm(self.Li[:,:self.N,:self.N].expand((batch_size,self.N,self.N)),Q_X)
        W_Y = torch.bmm(self.Li[:,:self.N,:self.N].expand((batch_size,self.N,self.N)),Q_Y)
        # reshape
        # W_X,W,Y: size [B,H,W,1,N]
        W_X = W_X.unsqueeze(3).unsqueeze(4).transpose(1,4).repeat(1,points_h,points_w,1,1)
        W_Y = W_Y.unsqueeze(3).unsqueeze(4).transpose(1,4).repeat(1,points_h,points_w,1,1)
        # compute weights for affine part
        A_X = torch.bmm(self.Li[:,self.N:,:self.N].expand((batch_size,3,self.N)),Q_X)
        A_Y = torch.bmm(self.Li[:,self.N:,:self.N].expand((batch_size,3,self.N)),Q_Y)
        # reshape
        # A_X,A,Y: size [B,H,W,1,3]
        A_X = A_X.unsqueeze(3).unsqueeze(4).transpose(1,4).repeat(1,points_h,points_w,1,1)
        A_Y = A_Y.unsqueeze(3).unsqueeze(4).transpose(1,4).repeat(1,points_h,points_w,1,1)

        # compute distance P_i - (grid_X,grid_Y)
        # grid is expanded in point dim 4, but not in batch dim 0, as points P_X,P_Y are fixed for all batch
        points_X_for_summation = points[:,:,:,0].unsqueeze(3).unsqueeze(4).expand(points[:,:,:,0].size()+(1,self.N))
        points_Y_for_summation = points[:,:,:,1].unsqueeze(3).unsqueeze(4).expand(points[:,:,:,1].size()+(1,self.N))

        if points_b==1:
            delta_X = points_X_for_summation-P_X
            delta_Y = points_Y_for_summation-P_Y
        else:
            # use expanded P_X,P_Y in batch dimension
            delta_X = points_X_for_summation-P_X.expand_as(points_X_for_summation)
            delta_Y = points_Y_for_summation-P_Y.expand_as(points_Y_for_summation)

        dist_squared = torch.pow(delta_X,2)+torch.pow(delta_Y,2)
        # U: size [1,H,W,1,N]
        dist_squared[dist_squared==0]=1 # avoid NaN in log computation
        U = torch.mul(dist_squared,torch.log(dist_squared))

        # expand grid in batch dimension if necessary
        points_X_batch = points[:,:,:,0].unsqueeze(3)
        points_Y_batch = points[:,:,:,1].unsqueeze(3)
        if points_b==1:
            points_X_batch = points_X_batch.expand((batch_size,)+points_X_batch.size()[1:])
            points_Y_batch = points_Y_batch.expand((batch_size,)+points_Y_batch.size()[1:])

        points_X_prime = A_X[:,:,:,:,0]+ \
                       torch.mul(A_X[:,:,:,:,1],points_X_batch) + \
                       torch.mul(A_X[:,:,:,:,2],points_Y_batch) + \
                       torch.sum(torch.mul(W_X,U.expand_as(W_X)),4)

        points_Y_prime = A_Y[:,:,:,:,0]+ \
                       torch.mul(A_Y[:,:,:,:,1],points_X_batch) + \
                       torch.mul(A_Y[:,:,:,:,2],points_Y_batch) + \
                       torch.sum(torch.mul(W_Y,U.expand_as(W_Y)),4)

        return torch.cat((points_X_prime,points_Y_prime),3)
