# -*- coding: utf-8 -*-
import csv
import re
import pymongo
from itertools import imap, groupby
import os
#from subprocess import Popen, PIPE


VALID_COLUMN_NO = 25


# remove keys whos values are "."
# and remove empty dictionaries
def dict_sweep(d):
    for key, val in d.items():
        if val == "-":
            del d[key]
        elif isinstance(val, dict):
            dict_sweep(val)
            if len(val) == 0:
                del d[key]
    return d


# convert string numbers into integers or floats
def value_convert(d):
    for key, val in d.items():
        try:
            d[key] = int(val)
        except (ValueError, TypeError):
            try:
                d[key] = float(val)
            except (ValueError, TypeError):
                pass
        if isinstance(val, dict):
            value_convert(val)
    return d


# if dict value is a list of length 1, unlist
def unlist(d):
    for key, val in d.items():
            if isinstance(val, list):
                if len(val) == 1:
                    d[key] = val[0]
            elif isinstance(val, dict):
                unlist(val)
    return d


#def phen_id(phenotype_ids):
#    p = phenotype_ids.strip(";").replace(";", ",").split(",")
#    phen_id = {}
#    for id in p:
#        key, value = id.split(":")
#        if key in phen_id:
#            if not isinstance(phen_id[key], list):
#                phen_id[key] = [phen_id[key]]
#            phen_id[key].append(value)
#        else:
#            phen_id[key] = value
#    return phen_id


# split id lists into dictionary
def other_id(other_ids):
    p = other_ids.strip(";").replace(";", ",").split(",")
    other_id = {}
    for id in p:
        try:
            ind = id.index(":")
            key, value = id[:ind], id[ind+1:]
            if key in other_id:
                if not isinstance(other_id[key], list):
                    other_id[key] = [other_id[key]]
                other_id[key].append(value)
            else:
                other_id[key] = value
        except:
            continue
    return other_id


# convert one snp to json
def _map_line_to_json(fields):
    assert len(fields) == VALID_COLUMN_NO
    chrom = fields[13]
    chromStart = fields[14]
    chromEnd = fields[15]

    HGVS = None
    cds = fields[18].split(":")
    cds = cds[1]

    seq = re.findall(r'[ATCGMNHYR]+|[0-9]+', cds)[-1]
    replace = re.findall(r'[ATCGMNYR=]+', cds)
    sub = re.search(r'[ATCGMNHYR]+>[ATCGMNHYR]+', cds)
    ins = re.search(r'ins[ATCGMNHYR]+|ins[0-9]+', cds)
    delete = fields[1] == 'deletion'
    indel = fields[1] == 'indel'
    dup = re.search(r'dup', cds)
    inv = re.search(r'inv|inv[0-9]+|inv[ATCGMNHYR]+', cds)
    if ins:
        delete = None
        indel = None
    elif delete:
        ins = None
        indel = None
    if sub:
        HGVS = "chr%s:g.%s%s" % (chrom, chromStart, sub.group())
    elif ins:
        HGVS = "chr%s:g.%s_%s%s" % (chrom, chromStart, chromEnd, ins.group())
    elif delete:
        HGVS = "chr%s:g.%s_%sdel" % (chrom, chromStart, chromEnd)
    elif indel:
        try:
            HGVS = "chr%s:g.%s_%sdel%s" % (chrom, chromStart, chromEnd, ins.group())
        except AttributeError:
            print "ERROR:", fields[1], cds
    elif dup:
        HGVS = "chr%s:g.%s_%sdup%s" % (chrom, chromStart, chromEnd, seq)
    elif inv:
        HGVS = "chr%s:g.%s_%sinv%s" % (chrom, chromStart, chromEnd, inv.group())
    elif replace:
        HGVS = "chr%s:g.%s_%s%s" % (chrom, chromStart, chromEnd, replace)
    else:
        print 'ERROR:', fields[1], cds

    # load as json data
    if HGVS is None:
        return

    one_snp_json = {

        "_id": HGVS,
        "clinvar":
            {
                "allele_id": fields[0],
                "genome":
                    {
                        "assembly": fields[12],
                        "chr": fields[13],
                        "start": fields[14],
                        "end": fields[15]
                    },
                "type": fields[1],
                "name": fields[2],
                "gene":
                    {
                        "id": fields[3],
                        "symbol": fields[4]
                    },
                "clinical_significance": fields[5].split(";"),
                "rs_dbsnp": fields[6],
                "nsv_dbvar": fields[7],
                "rcv_accession": fields[8].split(";"),
                "tested_in_gtr": fields[9],
                "phenotype_id": other_id(fields[10]),
                "origin": fields[11],
                "cytogenic": fields[16],
                "review_status": fields[17],
                "hgvs":
                    {
                        "coding": fields[18],
                        "protein": fields[19]
                    },
                "number_submitters": fields[20],
                "last_evaluated": fields[21],
                "guidelines": fields[22],
                "other_ids": other_id(fields[23]),
                "variant_id": fields[24]
            }
        }
    return dict_sweep(unlist(value_convert(one_snp_json)))


def merge_duplicate_rows(rows):
    rows = list(rows)
    first_row = rows[0]
    other_rows = rows[1:]
    for row in other_rows:
        for i in first_row['clinvar']:
            if i in row['clinvar']:
                if row['clinvar'][i] != first_row['clinvar'][i]:
                    aa = first_row['clinvar'][i]
                    if not isinstance(aa, list):
                        aa = [aa]
                    aa.append(row['clinvar'][i])
                    first_row['clinvar'][i] = aa
    return first_row


# open file, parse, pass to json mapper
def data_generator(input_file):
    #open_file = Popen(["sort", "-t", "\t", "-k14", "-k15", "-n", input_file], stdout=PIPE).stdout
    os.system("sort -t$'\t' -k14 -k15 -k20 -n %s > %s_sorted.tsv" % (input_file, input_file))
    #open_file = open(input_file)    
    open_file = open("%s_sorted.tsv" % (input_file))
    print input_file
    clinvar = csv.reader(open_file, delimiter="\t")
    clinvar.next()  # skip header
    clinvar = (row for row in clinvar
                if row[18] != '-' and
                row[18].find('?') == -1 and
                row[13] != "" and
                not re.search(r'p.', row[18]))
    json_rows = (row for row in imap(_map_line_to_json, clinvar) if row)
    row_groups = (it for (key, it) in groupby(json_rows, lambda row: row["_id"]))
    for one_snp_json in imap(merge_duplicate_rows, row_groups):
        yield one_snp_json
    open_file.close()


def load_collection(database, input_file_list, collection_name):
    """
    : param database: mongodb url
    : param input_file_list: variant docs, path to file
    : param collection_name: annotation source name
    """
    conn = pymongo.MongoClient(database)
    db = conn.variantdoc
    posts = db[collection_name] 
    for doc in data_generator(input_file_list):
        posts.insert(doc, manipulate=False, check_keys=False, w=0)