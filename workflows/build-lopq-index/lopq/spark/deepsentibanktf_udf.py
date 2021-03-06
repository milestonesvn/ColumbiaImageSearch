def udf(sc, data_path, sampling_ratio, seed):
    """
    MEMEX UDF function to load training data. 
    Loads data from a sequence file containing JSON formatted data with 
    a base64-encoded numpy arrays in field 'feat_field'.
    """
    from memex_udf import memex_udf
    feat_field = 'featnorm_tf'
    return memex_udf(sc, data_path, sampling_ratio, seed, feat_field)