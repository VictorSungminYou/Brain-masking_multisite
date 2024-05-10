#!/bin/env/ python3
  
import argparse
import os
import cv2
import sys
import glob
import numpy as np 
from tqdm import tqdm
from medpy.io import load, save
from model import Unet
from skimage.transform import resize
from skimage.measure import label
from skimage.morphology import binary_closing, binary_dilation, square, disk, cube

import subprocess

parser = argparse.ArgumentParser()

parser.add_argument('target_file', 
    nargs='+', 
    help='Path of a file or a folder of files.')

parser.add_argument('--remasking',
    dest='remasking',
    action='store_true',
    help='flag to indicate already masked images should be re masked, rewritting of all *_mask.nii found, defaults to False')

parser.add_argument('--no-remasking',
    dest='remasking',
    action='store_false',
    help='flag to indicate the skipping of already masked images, if there is a file of the same name with _mask, it will be skipped')

parser.set_defaults(remasking=False)

parser.add_argument('--post-processing',
    dest='post_processing',
    action='store_true',
    help='flag to indicate predicted mask should be post processed (morphological closing and defragged), defaults to True')

parser.add_argument('--no-post-processing',
    dest='post_processing',
    action='store_false',
    help='flag to indicate predicted mask should not be post processed (morphological closing and defragged)')
parser.set_defaults(post_processing=True)

parser.add_argument('--match',
    nargs='+',
    help='Specify if only files with certain words should be masked, not case sensitive')

parser.add_argument('--dilation_footprint',
    nargs=2,
    default=disk(3),
    help='Default is disk(2), can modify shape and size [--dilation_footprint square 2].')

parser.add_argument('--no-dilation',
                    dest='no_dilation',
                    action='store_false',
                    help='Flag indicates no dilation will be performed')

model_type = 'unet'

args = parser.parse_args()
target_file = args.target_file
remasking = args.remasking
post_processing = args.post_processing
match = args.match
dilation_footprint = args.dilation_footprint
no_dilation=args.no_dilation


if match:
    for i in range(len(match)):
        match[i] = match[i].lower()

if no_dilation :
    if type(dilation_footprint) is list:
        try:
            dilation_footprint[1] = int(dilation_footprint[1])

            if dilation_footprint[0] == 'square':
                footprint=square(dilation_footprint[1])
            elif dilation_footprint[0] == 'disk':
                    footprint = disk(dilation_footprint[1])
            else:
                print('Footprint shape not recognized, switching to default disk(2)')
                footprint = disk(2)

        except ValueError:
            print('That size is not supported, switching to default disk(2)')
            footprint = disk(2)
    else:
        footprint = disk(2)

def getImageData(fname):

    '''Returns the image data, image matrix and header of
    a particular file'''
    data, hdr = load(fname)
    # axes have to be switched from (256,256,x) to (x,256,256)
    data = np.moveaxis(data, -1, 0)

    norm_data = []
    # normalize each image slice
    for i in range(data.shape[0]):
        img_slice = data[i,:,:]
        norm_data.append(__normalize0_255(img_slice))

    # remake 3D representation of the image
    data = np.array(norm_data, dtype=np.float32)

    data = data[..., np.newaxis]
    return data, hdr

def __resizeData(image, target=(256, 256)):
    image = np.squeeze(image)
    resized_img = []
    for i in range(image.shape[0]):
        img_slice = cv2.resize(image[i,:,:], target)
        resized_img.append(img_slice)

    image = np.array(resized_img, dtype=np.float32)

    return image[..., np.newaxis]

def __normalize0_255(img_slice):
    '''Normalizes the image to be in the range of 0-255
    it round up negative values to 0 and caps the top values at the
    97% value as to avoid outliers'''
    img_slice[img_slice < 0] = 0
    flat_sorted = np.sort(img_slice.flatten())

    #dont consider values greater than 97% of the values
    top_3_limit = int(len(flat_sorted) * 0.97)
    limit = flat_sorted[top_3_limit]

    img_slice[img_slice > limit] = limit

    rows, cols = img_slice.shape
    #create new empty image
    new_img = np.zeros((rows, cols))
    max_val = np.max(img_slice)
    if max_val == 0:
        return new_img

    #normalize all values
    for i in range(rows):
        for j in range(cols):
            new_img[i,j] = int((
                float(img_slice[i,j])/float(max_val)) * 255)

    return new_img

def __postProcessing(mask):

    mask = np.squeeze(mask)
    x , y , z = np.shape(mask)
    dilated_mask = np.zeros((x,y,z))

    #Binary dilation
    if no_dilation :
        for slice in range(y):
            t = mask[:,slice,:]
            slice_dilated = binary_dilation(t,footprint)*1
            dilated_mask[:,slice,:] = slice_dilated
    else: 
        dilated_mask = mask

    #Binary closing
    pred_mask = binary_closing(np.squeeze(dilated_mask), cube(2))

    try:
        labels = label(pred_mask)
        pred_mask = (labels == np.argmax(np.bincount(labels.flat)[1:])+1).astype(np.float32)
    except:
        pred_mask = pred_mask

    return pred_mask

def binarize(directory,stat):
    #Change directory
    
    if stat == 1:
        os.chdir(directory)

    if stat == 0:
        os.chdir(os.path.dirname(directory))

    #Find files ending in 'mask.nii'
    #print("Present working directory:", os.getcwd())
    mask_files=glob.glob('*mask.nii')
    #print(f"Found mask files: {mask_files}")

    for file in mask_files:
        if not file.endswith('~'):
            #Apply fslmaths for the binarization of the file
            #print(f"Binarizing mask: {file}")
            subprocess.run(['/src/fslmaths', file, '-thr', '0.001', '-bin', file], check=True)

            #We remove the original file
            os.remove(file)

            #Decompress the new binarized file
            gzip_file = file + '.gz'
            #print(f"Unizpping mask: {gzip_file}")
            if os.path.exists(gzip_file):
                subprocess.run(['gunzip', gzip_file], check=True)


def main():
    full_paths = [os.path.join(os.getcwd(), path) for path in args.target_file]
    for path in full_paths:
        if os.path.isfile(path):
            all_files = full_paths
        if os.path.isdir(path):
            all_files = glob.glob(full_paths[0]+'/*.nii')
            all_gz_files = glob.glob(full_paths[0]+'/*.nii.gz')

            all_files += all_gz_files
    if match:
            all_files = [f for f in all_files if any(m in f.lower() for m in match)]

    # ignore masks
    files = [f for f in all_files if '_mask.nii' not in f]
    masks = [f for f in all_files if f not in files]

    if not remasking:
        files = [f for f in files if f[:-4] + '_mask.nii' not in masks]
    
    print('Found %d NIFTI files'%len(files))

    if len(files) == 0:
        print('No NIFTI files found, exiting')
        parser.print_help()
        sys.exit(0)

    if remasking:
        print('Remasking set to True, remasking all images found')
    else:
        print('Remasking set to False, masking only images without a [file name]_mask.nii file')

    if post_processing:
        print('Post processing set to True, post processing output masks')
    else:
        print('Post processing set to False, not post processing output masks')

    files = [f for f in files if '_mask.nii' not in f]

    if model_type == 'unet':
        print('Loading Unet model')
        model = Unet()

    skipped = []

    for img_path in tqdm(files):
        try:
            img, hdr = getImageData(img_path)
            resizeNeeded = False

            if model_type == 'unet':
                if img.shape[1] != 256 or img.shape[2] != 256:
                    original_shape = (img.shape[2], img.shape[1])
                    img = __resizeData(img)
                    resizeNeeded = True
            
            res = model.predict_mask(img)

            if post_processing:
                res = __postProcessing(res)

            if resizeNeeded:
                res = __resizeData(res.astype(np.float32), target = original_shape)
            
            #remove extra dimension
            res = np.squeeze(res)

            #return result into shape (256,256,X)
            res = np.moveaxis(res, 0, -1)

            #save result
            img_path = img_path[:img_path.rfind('.')]

            # this is for files ending in .nii.gz
            if '.nii' in img_path:
                img_path = img_path[:img_path.rfind('.')]

            save(res, img_path + '_mask.nii', hdr)
             
        except Exception as e:
            print(e)
            print('Not stopping')
            skipped.append(img_path)
            continue
        
    if len(skipped) > 0:
        print("%d images skipped."%len(skipped))


if __name__ == '__main__':
    main()
