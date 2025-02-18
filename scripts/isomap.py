import os, sys
import fire, tqdm
import numpy as np, matplotlib.pyplot as plt, scipy
import librosa, librosa.feature, librosa.display
import torch

import warnings

from fractions import Fraction
from kymatio.torch import TimeFrequencyScattering1D, Scattering1D
from sklearn.manifold import Isomap

try:
    import openl3
    skip_openl3 = False
except:
    # TODO implement skipping
    skip_openl3 = True
    warnings.warn("Could not import `openl3`, will skip experiment")


def sinusoid(f0, duration, sr):
    t = np.arange(0, duration, 1/sr)
    return np.sin(2 * np.pi * f0 * t)


def generate(f_c, f_m, gamma, bw=2, duration=2, sr=2**14):
    sigma0 = 0.1
    t = np.arange(-duration/2, duration/2, 1/sr)
    chirp_phase = 2*np.pi*f_c / (gamma*np.log(2)) * (2 ** (gamma*t) - 1)
    carrier = np.sin(chirp_phase)
    modulator = np.sin(2 * np.pi * f_m * t)
    window_std = sigma0 * bw / gamma
    window = scipy.signal.gaussian(duration*sr, std=window_std*sr)
    x = carrier * modulator * window
    return x


def generate_audio(f0s, fms, gammas, duration, sr):
    audio = np.zeros((len(f0s), len(fms), len(gammas), duration * sr))
    cmap = np.zeros((3, len(f0s) * len(fms) * len(gammas)))
    c = 0

    print('Generating Audio ...')
    for i, f0 in tqdm.tqdm(enumerate(f0s)):
        for j, fm in enumerate(fms):
            for k, gamma in enumerate(gammas):
                audio[i, j, k, :] = generate(f0, fm, gamma, sr=sr, duration=duration)
                audio[i, j, k, :] = audio[i, j, k, :] / np.linalg.norm(audio[i, j, k, :])
                cmap[0, c], cmap[1, c], cmap[2, c] = f0, fm, gamma
                c += 1
    return audio, cmap


def extract_mfcc(audio, f0s, fms, gammas, sr, n_mfcc = 20):
    mfcc = np.zeros((len(f0s), len(fms), len(gammas), n_mfcc))

    print('Extracting MFCCs ...')
    for i, f0 in tqdm.tqdm(enumerate(f0s)):
        for j, fm in enumerate(fms):
            for k, gamma in enumerate(gammas):
                mfcc[i, j, k,:] = np.mean(librosa.feature.mfcc(y=audio[i,j,k], sr=sr), axis=-1)
    return mfcc.reshape(-1, mfcc.shape[-1])


def extract_time_scattering(audio, duration, sr, **ts_kwargs):
    N = duration * sr
    scat = Scattering1D(shape=(N, ),
                        T=N,
                        Q=1,
                        # Q=8,
                        pad_mode='zero',
                        J=int(np.log2(N) - 1),).cuda()

    X = torch.tensor(audio).cuda()
    n_samples = X.shape[0]
    n_paths = scat(X[0]).shape[0]

    sx = torch.zeros(n_samples, n_paths)

    for i in tqdm.tqdm(range(n_samples)):
        sx[i, :] = scat(X[i, :])[:, 0]
    return sx.cpu().numpy()


def extract_jtfs(audio, duration, sr, **jtfs_kwargs):
    N = duration * sr
    jtfs = TimeFrequencyScattering1D(
        shape=(N,),
        T=N,
        Q=8,
        J=int(np.log2(N) - 1),
        pad_mode='zero',
        pad_mode_fr='zero',
        max_pad_factor=3,
        max_pad_factor_fr=None,
        sampling_filters_fr='resample').cuda()

    X = torch.tensor(audio).cuda()
    n_samples, n_paths = X.shape[0], jtfs(X[0]).shape[1]
    sx = torch.zeros(n_samples, n_paths)

    for i in tqdm.tqdm(range(n_samples)):
        sx[i, :] = jtfs(X[i, :])[:, :, 0]

    return sx.cpu().numpy()


def extract_openl3(audio, sr, **ol3_kwargs):
    X_ol3, _ = openl3.get_audio_embedding(
        list(audio),
        sr,
        batch_size=32,
        frontend='kapre',
        content_type='music')
    return np.stack(X_ol3).mean(axis=1)


def extract_strf(audio, duration, sr, **strf_kwargs):
    sys.path.insert(1, os.getcwd() + '/strf-like-model')
    import auditory

    X = audio
    n_samples = X.shape[0]
    S = auditory.strf(X[0, :], audio_fs=sr, duration=duration)
    S = np.concatenate((
        S[0].reshape((S[0].shape[0],     S[0].shape[1], -1)),
        S[1][:, :, np.newaxis]),
        axis=-1).mean(axis=0)
    n_freqs, n_paths = S.shape
    sx = np.zeros((n_samples, n_freqs, n_paths))

    for i in tqdm.tqdm(range(n_samples)):
        S = auditory.strf(X[i], audio_fs=sr, duration=duration)
        S = np.concatenate((
            S[0].reshape((S[0].shape[0], S[0].shape[1], -1)),
            S[1][:, :, np.newaxis]),
            axis=-1).mean(axis=0)
        sx[i, :] = S

    return sx.reshape((sx.shape[0], -1))


def plot_isomap(Y, cmap, out_dir):
    fig = plt.figure(figsize=plt.figaspect(0.5))
    ax = fig.add_subplot(1, 3, 1, projection='3d')
    ax.scatter3D(Y[:, 0], Y[:, 1], Y[:, 2], c=cmap[0], cmap='bwr');

    ax.set_xticklabels([])
    ax.set_yticklabels([])
    ax.set_zticklabels([])

    # f modulator
    ax = fig.add_subplot(1, 3, 2, projection='3d')
    ax.scatter3D(Y[:, 0], Y[:, 1], Y[:, 2], c=cmap[1], cmap='bwr');
    ax.set_xticklabels([])
    ax.set_yticklabels([])
    ax.set_zticklabels([])

    # chirp rate
    ax = fig.add_subplot(1, 3, 3, projection='3d')
    ax.scatter3D(Y[:, 0], Y[:, 1], Y[:, 2], c=cmap[2], cmap='bwr');
    ax.set_xticklabels([])
    ax.set_yticklabels([])
    ax.set_zticklabels([])
    plt.subplots_adjust(wspace=0, hspace=0)

    plt.savefig(os.path.join(out_dir, 'isomap.png'))


def plot_knn_regression(ratios, out_dir):
    plt.clf()
    yticklabels = ["1/3", "1/2", "1", "2", "3"]
    objs = ["Carrier freq.", "Modulation freq.", "Chirp rate"]

    N = len(ratios[list(ratios.keys())[0]][:, 0])

    fig, axes = plt.subplots(ncols=3, figsize=plt.figaspect(.5), sharey=True)
    for i, ratio in enumerate(ratios.values()):
        for idx, ax in enumerate(axes.flat):
            ax.plot(np.random.uniform(i - 0.1, i + 0.1, N),
                    np.log2(ratio[:, idx]), ".", markersize=1)

            ax.set_yticks(np.log2(np.array([float(Fraction(label))
                                            for label in yticklabels])))
            ax.set_xticks([i for i in range(len(ratios))])
            ax.set_xticklabels(list(ratios.keys()), fontsize=13)
            ax.set_yticklabels(yticklabels)
            ax.grid(linestyle="--")
            ax.set_title(objs[idx], fontsize=14)
            if idx == 0:
                ax.set_ylabel("Relative estimate", fontsize=14)

    fig.subplots_adjust(left=0, right=1, bottom=0, top=1, wspace=.01)
    plt.tight_layout()
    plt.savefig(os.path.join(out_dir, 'knn.png'))


def run_isomaps(X, cmap, out_dir, n_neighbors=40):

    Y = {}
    ratios = {}
    models = {}

    for feat in X.keys():
        feat_dir = os.path.join(out_dir, feat)

        os.makedirs(feat_dir, exist_ok=True)
        models[feat] = Isomap(n_components=3, n_neighbors=n_neighbors)
        Y[feat] = models[feat].fit_transform(X[feat])

        plot_isomap(Y[feat], cmap, feat_dir)

        knn = models[feat].nbrs_.kneighbors()
        ratios[feat] = np.vstack([
            np.exp(np.mean(np.log(cmap[:, knn[1][i, :]]), axis=1)) / cmap[:, i]
            for i in range(X[feat].shape[0])
        ])
    plot_knn_regression(ratios, out_dir)


def run_isomap(
    n_steps = 16,
    f0_min = 512,
    f0_max = 1024,
    fm_min = 4,
    fm_max = 16,
    gamma_min = 0.5,
    gamma_max = 4,
    bw = 2,
    duration = 4,
    sr = 2**13,
    out_dir = '/img'):


    out_dir = os.getcwd() + out_dir

    if not os.path.exists(out_dir):
        os.makedirs(out_dir)

    f0s = np.logspace(np.log10(f0_min), np.log10(f0_max), n_steps)
    fms = np.logspace(np.log10(fm_min), np.log10(fm_max), n_steps)
    gammas = np.logspace(np.log10(gamma_min), np.log10(gamma_max), n_steps)
    audio, cmap = generate_audio(f0s, fms, gammas, duration, sr)

    mfcc = extract_mfcc(audio, f0s, fms, gammas, sr)
    ts = extract_time_scattering(audio.reshape(-1, audio.shape[-1]), duration, sr)
    jtfs = extract_jtfs(audio.reshape(-1, audio.shape[-1]), duration, sr)    
    ol3 = extract_openl3(audio.reshape(-1, audio.shape[-1]), sr)
    strf = extract_strf(audio.reshape(-1, audio.shape[-1]), duration, sr)

    X = {"MFCC": mfcc, "TS": ts, "JTFS": jtfs, "OPEN-L3": ol3, "STRF": strf}
    # X = {"MFCC": mfcc, "TS": ts, "JTFS": jtfs}
    # X = {"TS": ts, "JTFS": jtfs}

    run_isomaps(X, cmap, out_dir)


def main():
  fire.Fire(run_isomap)


if __name__ == "__main__":
    main()
