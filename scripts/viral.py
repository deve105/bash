#! /usr/bin/env python3
'''
Pipeline for designing primers from a collection of viral sequences
'''
VERSION = '0.0.1'

# imports
from datetime import datetime
from gzip import open as gopen
from math import log2
from os import mkdir
from os.path import abspath, expanduser, getsize, isdir, isfile
from seaborn import relplot
from subprocess import call, check_output, STDOUT
from sys import argv, stdin, stderr
import argparse
import matplotlib.pyplot as plt

# constants
MIN_MAFFT_OUTPUT_SIZE = 100
MIN_MAFFT_VERSION = 7.467
MIN_PRIMER3_OUTPUT_SIZE = 100
NUC_COLORS = {'A':'red', 'C':'blue', 'G':'purple', 'T':'yellow', '-':'black'}
NUCS_SORT = ['A', 'C', 'G', 'T', '-']; NUCS = set(NUCS_SORT)
NUM_SEQS_PROGRESS = 500

# defaults
DEFAULT_BLAST_WORD_SIZE = 11
DEFAULT_BUFSIZE = 1048576 # 1 MB
DEFAULT_PRIMER3_PRIMER_MAX_SIZE = 36
DEFAULT_PRIMER3_PRIMER_MIN_SIZE = 18
DEFAULT_PRIMER3_PRIMER_OPT_SIZE = 20
DEFAULT_PRIMER3_PRIMER_PRODUCT_MIN_SIZE = 50
DEFAULT_PRIMER3_PRIMER_PRODUCT_MAX_SIZE = 170
DEFAULT_PRIMER3_STEP_SIZE = 500
DEFAULT_PRIMER3_WINDOW_SIZE = 1000

# return the current time as a string
def get_time():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# helper class for logging
class Log:
    def __init__(self, loggern, quiet=False, bufsize=DEFAULT_BUFSIZE):
        self.log_f = open(loggern, 'w', buffering=bufsize)
        if quiet:
            self.ostream = None
        else:
            self.ostream = stderr
    def __del__(self):
        self.log_f.close()
    def write(self, s, include_time=True):
        if include_time:
            s = '[%s] %s' % (get_time(), s)
        self.log_f.write(s)
        if self.ostream is not None:
            self.ostream.write(s)
        self.flush()
    def flush(self):
        self.log_f.flush()
        if self.ostream is not None:
            self.ostream.flush()

# parse user args
def parse_args():
    # check for -v/--version
    if '-v' in argv or '--version' in argv:
        print("ViralPrimerDesign v%s" % VERSION); exit(0)

    # use argparse to parse user arguments
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('-s', '--sequences', required=False, type=str, default='stdin', help="Input Sequences (FASTA format)")
    parser.add_argument('-r', '--reference', required=True, type=str, help="Reference Genome (FASTA format)")
    parser.add_argument('-o', '--outdir', required=True, type=str, help="Output Directory")
    parser.add_argument('--skip_alignment', action="store_true", help="Skip Alignment (if input FASTA is already aligned)")
    parser.add_argument('-t', '--threads', required=False, type=int, default=1, help="Number of Threads (for MSA)")
    parser.add_argument('--primer3_window_size', required=False, type=int, default=DEFAULT_PRIMER3_WINDOW_SIZE, help="Primer3 Sliding Window Size")
    parser.add_argument('--primer3_step_size', required=False, type=int, default=DEFAULT_PRIMER3_STEP_SIZE, help="Primer3 Sliding Window Step Size")
    parser.add_argument('--primer3_primer_opt_size', required=False, type=int, default=DEFAULT_PRIMER3_PRIMER_OPT_SIZE, help="Primer3 Optimal Primer Length (PRIMER_OPT_SIZE)")
    parser.add_argument('--primer3_primer_min_size', required=False, type=int, default=DEFAULT_PRIMER3_PRIMER_MIN_SIZE, help="Primer3 Minimum Primer Length (PRIMER_MIN_SIZE)")
    parser.add_argument('--primer3_primer_max_size', required=False, type=int, default=DEFAULT_PRIMER3_PRIMER_MAX_SIZE, help="Primer3 Maximum Primer Length (PRIMER_MAX_SIZE)")
    parser.add_argument('--primer3_primer_product_min_size', required=False, type=int, default=DEFAULT_PRIMER3_PRIMER_PRODUCT_MIN_SIZE, help="Primer3 Minimum Primer Product Length (left part of PRIMER_PRODUCT_SIZE_RANGE)")
    parser.add_argument('--primer3_primer_product_max_size', required=False, type=int, default=DEFAULT_PRIMER3_PRIMER_PRODUCT_MAX_SIZE, help="Primer3 Maximum Primer Product Length (right part of PRIMER_PRODUCT_SIZE_RANGE)")
    parser.add_argument('--blast_word_size', required=False, type=int, default=DEFAULT_BLAST_WORD_SIZE, help="BLAST Word Size")
    parser.add_argument('-q', '--quiet', action="store_true", help="Quiet (hide verbose messages)")
    parser.add_argument('-v', '--version', action="store_true", help="Show Version Number")
    args = parser.parse_args()

    # check user arguments for validity
    if args.sequences != 'stdin' and not args.sequences.startswith('/dev/fd/') and not isfile(args.sequences):
        raise ValueError("File not found: %s" % args.sequences)
    if not args.reference.startswith('/dev/fd/') and not isfile(args.reference):
        raise ValueError("File not found: %s" % args.reference)
    args.outdir = abspath(expanduser(args.outdir))
    if isdir(args.outdir) or isfile(args.outdir):
        raise ValueError("Output exists: %s" % args.outdir)
    if args.threads < 1:
        raise ValueError("Number of threads must be positive integer: %s" % args.threads)
    if args.primer3_window_size < 1:
        raise ValueError("Window size must be positive integer: %s" % args.primer3_window_size)
    if args.primer3_step_size > args.primer3_window_size:
        raise ValueError("Window step size must be <= window size: %s" % args.primer3_step_size)
    if args.primer3_primer_opt_size < 1:
        raise ValueError("Optimal primer length must be positive integer: %s" % args.primer3_primer_opt_size)
    if args.primer3_primer_min_size > args.primer3_primer_opt_size:
        raise ValueError("Minimum primer length must be at most optimal primer length: %s" % args.primer3_primer_min_size)
    if args.primer3_primer_max_size < args.primer3_primer_opt_size:
        raise ValueError("Maximum primer length must be at least optimal primer length: %s" % args.primer3_primer_max_size)
    if args.primer3_primer_product_min_size <= args.primer3_primer_max_size:
        raise ValueError("Minimum primer product length must be greater than maximum primer length: %s" % args.primer3_primer_product_min_size)
    if args.primer3_primer_product_max_size < args.primer3_primer_product_min_size:
        raise ValueError("Maximum primer product length must be at least minimum primer product length: %s" % args.primer3_primer_product_max_size)
    if args.blast_word_size < 4:
        raise ValueError("BLAST word size must be at least 4: %s" % args.blast_word_size)
    return args

# align using MAFFT
def align_mafft(in_fn, ref_fn, out_fn, threads=1, logger=None, bufsize=DEFAULT_BUFSIZE):
    if in_fn.lower().endswith('.gz'):
        raise ValueError("MAFFT doesn't support gzipped input sequences: %s" % in_fn)
    try:
        mafft_version = check_output(['mafft', '--version'], stderr=STDOUT).decode()
    except:
        raise RuntimeError("Unable to execute 'mafft'. Are you sure it's in your PATH?")
    mafft_version = float(mafft_version.split()[0].lstrip('v'))
    if mafft_version < MIN_MAFFT_VERSION:
        raise RuntimeError("Must use MAFFT v%s or higher, but detected v%s" % (MIN_MAFFT_VERSION, mafft_version))
    for fn in [in_fn, ref_fn]:
        if not fn.startswith('/dev/fd/') and not isfile(fn):
            raise ValueError("File not found: %s" % fn)
    if isfile(out_fn) or isdir(out_fn):
        raise ValueError("Output exists: %s" % fn)
    command = ['mafft', '--thread', str(threads), '--6merpair', '--addfragments']
    if logger is not None:
        logger.write("Aligning sequences from: %s\n" % in_fn)
        logger.write("Reference genome: %s\n" % ref_fn)
    e_fn = '%s/mafft.log' % '/'.join(out_fn.split('/')[:-1])
    if out_fn.lower().endswith('.gz'):
        command = '%s "%s" "%s" 2> "%s" | gzip -9 > "%s"' % (' '.join(command), in_fn, ref_fn, e_fn, out_fn)
        if logger is not None:
            logger.write("MAFFT command: %s\n" % command)
        call(command, shell=True)
    else:
        command += [in_fn, ref_fn]
        if logger is not None:
            logger.write("MAFFT command: %s\n" % ' '.join(command))
        o = open(out_fn, 'w', buffering=bufsize); e = open(e_fn, 'w', buffering=bufsize); call(command, stdout=o, stderr=e); o.close(); e.close()
    if getsize(out_fn) < MIN_MAFFT_OUTPUT_SIZE:
        raise ValueError("MAFFT crashed. See log: %s" % e_fn)
    if logger is not None:
        logger.write("Multiple sequence alignment written to: %s\n" % out_fn)

# iterate over sequences of FASTA
def iter_fasta(in_fn, bufsize=DEFAULT_BUFSIZE):
    if in_fn == 'stdin':
        in_f = stdin
    elif not in_fn.startswith('/dev/fd/') and not isfile(in_fn):
        raise ValueError("File not found: %s" % in_fn)
    elif in_fn.lower().endswith('.gz'):
        in_f = gopen(in_fn, 'rt')
    else:
        in_f = open(in_fn, 'r', buffering=bufsize)
    seq = None
    for line in in_f:
        l = line.strip()
        if len(l) == 0:
            continue
        if l.startswith('>'):
            if seq is not None:
                yield seq
            seq = ''
        else:
            seq += l.upper()
    in_f.close()

# count bases at each position of MSA
def count_bases(in_fn, logger=None):
    if logger is not None:
        logger.write("Counting bases from: %s\n" % in_fn)
    counts = None # counts[pos][nuc] = count
    for seq_ind, seq in enumerate(iter_fasta(in_fn)):
        if counts is None:
            counts = [{c:0 for c in NUCS_SORT} for _ in range(len(seq))]
        elif len(seq) != len(counts):
            raise ValueError("MSA sequences have differing lengths: %s" % in_fn)
        for i, c in enumerate(seq):
            if c not in NUCS:
                continue
            counts[i][c] += 1
        if logger is not None and (seq_ind+1) % NUM_SEQS_PROGRESS == 0:
            logger.write("Parsed %d sequences...\n" % (seq_ind+1))
    return counts

# write base counts
def write_counts(counts, out_fn, delim='\t', logger=None, bufsize=DEFAULT_BUFSIZE):
    if isfile(out_fn) or isdir(out_fn):
        raise ValueError("Output exists: %s" % out_fn)
    if logger is not None:
        logger.write("Writing base counts to file...\n")
    out_f = open(out_fn, 'w', buffering=bufsize); out_f.write('Position (1-indexed)%s%s\n' % (delim, delim.join(NUCS_SORT)))
    for i, curr in enumerate(counts):
        out_f.write('%d%s%s\n' % (i+1, delim, delim.join(str(curr[c]) if c in curr else '0' for c in NUCS_SORT)))
    out_f.close()
    if logger is not None:
        logger.write("Base counts written to: %s\n" % out_fn)

# compute consensus sequence
def compute_consensus(counts, out_fn, logger=None, bufsize=DEFAULT_BUFSIZE):
    if logger is not None:
        logger.write("Computing consensus sequence...\n")
    seq = ''.join(c for count in counts for c in 'ACGT' if count[c] == max(count.values()))
    out_f = open(out_fn, 'w', buffering=bufsize); out_f.write('>Consensus - ViralPrimerDesign v%s\n%s\n' % (VERSION, seq)); out_f.close()
    if logger is not None:
        logger.write("Consensus sequence written to: %s\n" % out_fn)
    return seq

# compute Shannon entorpy from MSA base counts
def compute_entropies(counts, logger=None):
    if logger is not None:
        logger.write("Computing Shannon entropies...\n")
    ents = [None for _ in range(len(counts))] # ents[pos] = (max base freq, entropy)
    for i, curr in enumerate(counts):
        ent = 0; tot = sum(curr.values()); m = max(curr.values())/tot
        for c in curr:
            if curr[c] != 0:
                p = curr[c]/tot; ent -= (p*log2(p))
        ents[i] = ent
    return ents

# write Shannon entropies
def write_entropies(ents, out_fn, delim='\t', logger=None, bufsize=DEFAULT_BUFSIZE):
    if isfile(out_fn) or isdir(out_fn):
        raise ValueError("Output exists: %s" % out_fn)
    if logger is not None:
        logger.write("Writing entropies to file...\n")
    out_f = open(out_fn, 'w', buffering=bufsize); out_f.write('Position (1-indexed)%sEntropy\n' % delim)
    for i, curr in enumerate(ents):
        out_f.write('%d%s%s\n' % (i+1, delim, curr))
    out_f.close()
    if logger is not None:
        logger.write("Entropies written to: %s\n" % out_fn)

# plot entropies
def plot_entropies(ents, out_fn, logger=None):
    if isfile(out_fn) or isdir(out_fn):
        raise ValueError("Output exists: %s" % out_fn)
    if logger is not None:
        logger.write("Plotting entropies...\n")
    x = [i+1 for i, e in enumerate(ents) if e is not None]
    y = [e for i, e in enumerate(ents) if e is not None]
    fig = relplot(x=x, y=y, kind='line')
    plt.xlabel("Position (1-indexed)")
    plt.ylabel("Shannon Entropy")
    fig.savefig(out_fn, format='pdf', bbox_inches='tight')
    if logger is not None:
        logger.write("Entropy plot saved to: %s\n" % out_fn)

