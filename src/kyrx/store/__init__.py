"""Content-addressed blob store.

I2: artifacts are immutable and named by blake3(content); the garbage
collector refcounts blobs from the live entries.
"""
