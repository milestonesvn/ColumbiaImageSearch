import MySQLdb
import happybase
from Queue import *
from threading import Thread
import json
import time
import os
import numpy as np
import sys
sys.path.insert(0, os.path.abspath('../memex_tools'))
import sha1_tools
hbase_conn_timeout = None
tab_aaron_name = 'aaron_memex_ht-images'
tab_hash_name = 'image_id_sha1'
tab_missing_sha1_name = 'ht-images_missing_sha1'
tab_missing_sim_name = 'ht-images_missing_sim_images'
nb_threads = 8
pool = happybase.ConnectionPool(size=nb_threads,host='10.1.94.57',timeout=hbase_conn_timeout)
sha1_tools.pool = pool
global_var = json.load(open('../../conf/global_var_all.json'))
sha1_tools.global_var = global_var
sha1_tools.tab_aaron_name = tab_aaron_name
row_count = 0
missing_sha1_count = 0
missing_sim_count = 0

batch_size = 10000

### fill sha1 sim in aaron_memex_ht-images
# scan aaron_memex_ht-images
# get sha1 of row-key
# get sha1 for each image in meta:columbia_near_dups
# compact meta:columbia_near_dups into meta:columbia_near_dups_sha1 and maintain distances info of corresponding images from meta:columbia_near_dups_dist in meta:columbia_near_dups_sha1_dist

def process_one_row(one_row):
    global missing_sha1_count,missing_sim_count
    # should indicate row being already processed
    if 'meta:sha1' in one_row[1].keys():
        #print "Skipping row {} with keys {}.".format(one_row[0],one_row[1].keys())
        return
    row_sha1, from_url = get_row_sha1(one_row)
    if not row_sha1 or row_sha1=='NULL' or row_sha1=='null':
        print "Could not get sha1 for image_id {}.".format(one_row[0])
        missing_sha1_count += 1
        # push to missig sha1
        sha1_tools.save_missing_SHA1_to_hbase_missing_sha1([one_row[0]],tab_missing_sha1_name)
        return
    if row_sha1 is not None and from_url:
        #print "Computed new sha1 for image_id {}.".format(one_row[0])
        # push to image_hash
        sha1_tools.save_SHA1_to_hbase_imagehash(one_row[0],row_sha1,tab_hash_name)
    # add sha1 to row
    if 'meta:columbia_near_dups' not in one_row[1].keys() or one_row[1]['meta:columbia_near_dups']=='':
        #print "Similar images not computed for image_id {}.".format(one_row[0])
        missing_sim_count += 1
        save_missing_sim_images(one_row[0])
        return
    sim_image_ids = [str(x) for x in one_row[1]['meta:columbia_near_dups'].split(',')]
    sim_sha1s, missing_sim_iids, new_sha1s = sha1_tools.get_batch_SHA1_from_imageids(sim_image_ids)
    dists = one_row[1]['meta:columbia_near_dups_dist'].split(',')
    if new_sha1s:
        sha1_tools.save_batch_SHA1_to_hbase_image_hash(new_sha1s,tab_hash_name)
    if missing_sim_iids:
        # push missing sha1
        sha1_tools.save_missing_SHA1_to_hbase_missing_sha1(missing_sim_iids,tab_missing_sha1_name)
        # realign dists
        dists = [d for d,i in enumerate(dists) if sim_image_ids[i] not in missing_sim_iids]
    # are there some null sha1s here?
    unique_sim_sha1s, sim_sha1s_pos = np.unique(sim_sha1s,return_index=True)                
    #print row_count, one_row[0], row_sha1, from_url, sim_sha1s, missing_sim_sha1s, new_sha1s
    # ValueError: could not convert string to float?
    try:
        dists = np.asarray([np.float32(x) for x in dists])
    except Exception as inst:
        print "[process_one_row] {}".format(inst)
        print "[process_one_row] {}".format(dists)
        print "[process_one_row] {}".format(missing_sim_iids)
        print "[process_one_row] {},{}".format(one_row[0],one_row[1]['meta:columbia_near_dups_dist'].split(','))
        raise ValueError('Incorrect dists')
    sim_sha1s_sorted_pos = np.argsort(dists[sim_sha1s_pos])
    with pool.connection() as connection:
        tab_aaron = connection.table(tab_aaron_name)
        tab_aaron.put(one_row[0],{'meta:sha1': str(row_sha1), 'meta:columbia_near_dups_sha1': ','.join([str(x) for x in list(unique_sim_sha1s[sim_sha1s_sorted_pos])]), 'meta:columbia_near_dups_sha1_dist': ','.join([str(x) for x in list(dists[sim_sha1s_pos[sim_sha1s_sorted_pos]])])})
    return

def process_batch_rows(list_rows):
    for one_row in list_rows:
        process_one_row(one_row)

def process_batch_worker():
    while True:
        batch_start = time.time()
        tupInp = q.get()
        try:
            #print "Starting to process batch of rows {}.".format([x[0] for x in tupInp[0]])
            process_batch_rows(tupInp[0])
            tel = time.time()-batch_start
            print "Batch from row {} (count: {}) done in: {}.".format(tupInp[0][0][0],tupInp[1],tel)
            q.task_done()
        except Exception as inst:
            print "Batch from row {} (count: {}) FAILED.".format(tupInp[0][0][0],tupInp[1])
            exc_type, exc_obj, exc_tb = sys.exc_info()  
            fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
            print "{} in {} line {}.\n".format(exc_type, fname, exc_tb.tb_lineno)
            print "[process_batch_worker] {}.".format(inst)

def save_missing_sim_images(image_id,tab_missing_sim_name=tab_missing_sim_name):
    with pool.connection(timeout=hbase_conn_timeout) as connection:
        tab_missing_sim = connection.table(tab_missing_sim_name)
        tab_missing_sim.put(str(image_id), {'info:missing_sim': image_id})

def get_row_sha1(row):
    row_sha1 = sha1_tools.get_SHA1_from_hbase_imagehash(row[0])
    from_url = False
    # TODO check if image:orig exists, and compute sha1 from it if it does.
    if not row_sha1 and 'meta:location' in row[1].keys():
        row_sha1 = sha1_tools.get_SHA1_from_URL(row[1]['meta:location'])
        from_url = True
        print "Got new SHA1 {} for image_id {} from_url  {}.".format(row_sha1,row[0],row[1]['meta:location'])
    #print "Got SHA1 {} from image_id {} (from_url is {}).".format(row_sha1,row[0],from_url)
    return row_sha1, from_url

if __name__ == '__main__':
    start_time = time.time()
    last_row = None
    #issue_file = "issue_start_row.txt"
    #fif = open(issue_file,"rt")
    done = False
    list_rows = []

    # Prepare queue
    q = Queue()
    for i in range(nb_threads):
        t=Thread(target=process_batch_worker)
        t.daemon=True
        t.start()

    while not done:
        try:
            with pool.connection() as connection:
                tab_aaron = connection.table(tab_aaron_name)
                # to do filter to select only columns needed
                for one_row in tab_aaron.scan(row_start=last_row):
                #for one_row in tab_aaron.scan(row_start=fif.readline()):
                    row_count += 1
                    list_rows.append(one_row)
                    has_slept = False
                    if row_count%(batch_size)==0:
                        while q.qsize()>nb_threads+2:
                            print "Queue seems quite full. Waiting 30 seconds."
                            sys.stdout.flush()
                            time.sleep(30)
                            has_slept = True
                        print "Pushing batch starting from row {}.".format(list_rows[0][0])
                        print "Scanned {} rows so far (misssing sha1: {}, sim: {}).".format(row_count,missing_sha1_count,missing_sim_count)
                        q.put((list_rows,row_count))
                        last_row = list_rows[-1][0]
                        list_rows = []
                        sys.stdout.flush()
                        # should we break after sleeping? scan may have timed out...
                        #if has_slept:
                        #    break
                if has_slept:
                    raise ValueError("Waited to long. Just restart scanning with new connection to avoid error.") 
                done = True
                if list_rows:
                    # push last batch
                    print "Pushing batch starting from row {}.".format(list_rows[0][0])
                    print "Scanned {} rows so far (misssing sha1: {}, sim: {}).".format(row_count,missing_sha1_count,missing_sim_count)
                    q.put((list_rows,row_count))
                    list_rows = []
                    sys.stdout.flush()
        except Exception as inst:
            print "[Caught error] {}\n".format(inst)
            exc_type, exc_obj, exc_tb = sys.exc_info()  
            fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
            print "{} in {} line {}.\n".format(exc_type, fname, exc_tb.tb_lineno)
            time.sleep(2)
            # Should we reinitialize the pool?
            pool = happybase.ConnectionPool(size=nb_threads,host='10.1.94.57',timeout=hbase_conn_timeout)
            sha1_tools.pool = pool
        if done:
            print "Joining i.e. waiting for all jobs to finish."
            sys.stdout.flush()
            q.join()
            tel = time.time()-start_time
            print "Scanned {} rows total (misssing sha1: {}, sim: {}). Average time per row is: {}. Total time is: {}.".format(row_count,missing_sha1_count,missing_sim_count,tel/row_count,tel)
