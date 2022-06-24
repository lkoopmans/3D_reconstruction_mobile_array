import ffmpeg
from scipy.io import wavfile
from scipy.signal import fftconvolve
import json
import os
import numpy as np
import subprocess
import cv2
from multiprocessing import Pool
import time


class Generate3dMap:
    def __init__(self, input_videos_folder):
        self.input_videos_folder = input_videos_folder
        self.input_videos = []
        self.output_videos = []
        self.fps = []
        self.file_extensions = ['A', 'B', 'C']

        self.output_images = input_videos_folder.replace('input', 'output')+'/images'

        if not os.path.exists(self.output_images):
            os.mkdir(self.output_images)

        for i in range(len(self.file_extensions)):
            if not os.path.exists(self.output_images+'/'+self.file_extensions[i]):
                os.mkdir(self.output_images+'/'+self.file_extensions[i])

    def synchronize_videos(self, start=None, duration=None, dry_run=False):
        input_videos_folder = self.input_videos_folder
        t_start = time.time()
        print('Building directories...')
        video_files_list = os.listdir(input_videos_folder)
        output_videos_folder = input_videos_folder.replace('input', 'output')

        if not os.path.exists(output_videos_folder):
            os.mkdir(output_videos_folder)

        video_file_path = []

        for i in range(len(video_files_list)):
            video_file_path.insert(i, input_videos_folder + '/' + video_files_list[i])

        self.input_videos = video_file_path

        metadata = [parse_metadata(video_file_path) for video_file_path in video_file_path]

        self.fps = np.round(metadata[0]['fps'])

        assert len(set([metadata[idx]['fps'] for idx in range(len(metadata))])) == 1, \
            'videos must have the same frame rate'
        assert len(set([metadata[idx]['fps'] for idx in range(len(metadata))])) == 1, \
            'audio streams must have the same sample rate'

        print('Verify input data...')
        start, duration = check_input([start, duration], video_file_path)  # Start = 0, otherwise wavfiles are empty

        print('Extract audio signals...')
        signals = [extract_audio(video_file_path, start=0, duration=d) \
                   for video_file_path, s, d in zip(video_file_path, start, duration)]
        offsets = [0]
        ref = signals[0]
        signals = signals[1:]

        print('Compute maximum cross-correlation between the audio signals and shift the videos accordingly...')
        for sig in signals:
            offsets.append(compute_offset(ref, sig, metadata[0]['sample_rate']))
        offsets = np.array(offsets) - np.min(offsets)

        print('Write output...')
        c = 0

        for idx, video_file_path in enumerate(video_file_path):
            if not dry_run:
                if offsets[idx] * metadata[idx]['fps'] < 1:
                    copy_video(video_file_path)

                else:
                    cut_video(video_file_path, offsets[idx])

            video_file_out = video_file_path.replace('input', 'output')
            cut_file = os.path.splitext(video_file_out)[0] + '_cut' + os.path.splitext(video_file_out)[1]
            self.output_videos.insert(c, cut_file)

            c += 1
            print('Video ', c, '/', idx+1)

        print('Done. Time elapsed: ', np.round(time.time() - t_start, 3), 's')
        return

    def generate_images(self, attack_stages, attack_stage_sample_interval, dry_run=False):
        print('--------------------------------------------')
        print('Generate single images from video data...')
        t_start = time.time()
        fps = int(self.fps)

        t_sample = np.arange(0, fps * np.max(attack_stages))
        t_valid = np.empty

        for i in range(len(attack_stage_sample_interval)):
            t_valid = np.append(t_valid, np.arange(attack_stages[i] * fps, attack_stages[i + 1] * fps,
                                                   attack_stage_sample_interval[i]))

        i = 0

        for file in self.output_videos:
            c = 0

            print('Generating ' + str(len(t_valid)) + ' images from video set', self.file_extensions[i], '...')
            cap = cv2.VideoCapture(file)
            success, image = cap.read()

            while success  and not dry_run:
                if any(t_valid == c):
                    cv2.imwrite(
                        self.output_images + '/' + self.file_extensions[i] + "/" + self.file_extensions[i] + str(
                            c) + '.jpg', image)
                success, image = cap.read()
                c += 1
            i += 1

        print('Done. Time elapsed: ', np.round(time.time() - t_start, 3), 's')

def parse_metadata(video_file):
    temp_file = os.path.splitext(video_file)[0] + '.json'
    subprocess.run('ffprobe -v quiet -print_format json -show_format -show_streams {} > {}'.format(video_file,
                                                                                                   temp_file),
                   shell=True)
    metadata = json.load(open(temp_file, 'r'))
    os.remove(temp_file)
    fps = np.array(metadata['streams'][0]['r_frame_rate'].split('/')).astype(int)
    channels = int(metadata['streams'][1]['channels'])
    sample_rate = int(metadata['streams'][1]['sample_rate'])
    fps = fps[0] / fps[1]
    return {'fps': fps,
            'sample_rate': sample_rate,
            'channels': channels}


def extract_audio(video_file, start, duration):
    metadata = parse_metadata(video_file)

    temp_file = os.path.splitext(video_file)[0] + '.wav'
    subprocess.run('ffmpeg -y -v quiet -i {} -vn -c:a pcm_s16le -ss {} -t {} {}'.format(video_file,
                                                                                        start,
                                                                                        duration,
                                                                                        temp_file),
                   shell=True)
    sample_rate, signal = wavfile.read(temp_file)
    os.remove(temp_file)
    assert (sample_rate == metadata['sample_rate']) & (signal.shape[1] == metadata['channels']), \
        'audio stream did not match video metadata'
    if metadata['channels'] > 1:
        signal = signal.mean(axis=1)
    return signal


def compute_offset(audio_1, audio_2, sample_rate):
    corr = fftconvolve(audio_1, audio_2[::-1], mode='full')
    offset = np.argmax(corr)
    offset_seconds = ((2 * audio_2.size - 1) // 2 - offset) / sample_rate
    return offset_seconds


def check_input(args, video_files):
    checked_args = []
    for arg in args:
        if isinstance(arg, (float, int)):
            arg = [arg] * len(video_files)
        elif arg is None:
            arg = [30] * len(video_files)  # Why is this 30?
        assert isinstance(arg, list) and (len(arg) == len(video_files)), \
            'start and duration must be either None, or of type int, float or list with length of video files'
        checked_args.append(arg)
    return checked_args


def cut_video(video_file, offset):
    video_file_out = video_file.replace('input', 'output')
    cut_file = os.path.splitext(video_file_out)[0] + '_cut' + os.path.splitext(video_file_out)[1]
    subprocess.run('ffmpeg -y -v quiet -i {} -ss {} -c:v libx264 -an -crf 18 -preset ultrafast {}'.format(video_file,
                                                                                                          offset,
                                                                                                          cut_file),
                   shell=True)
    return cut_file


def copy_video(video_file):
    video_file_out = video_file.replace('input', 'output')
    cut_file = os.path.splitext(video_file_out)[0] + '_cut' + os.path.splitext(video_file_out)[1]
    subprocess.run('cp {} {}'.format(video_file, cut_file), shell=True)
    return cut_file


