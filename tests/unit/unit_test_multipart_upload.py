import unit
import filecmp
import math
import os
import tempfile
from nose.tools import assert_raises, assert_true, assert_greater_equal, assert_equals, assert_is_instance
from synapseclient.multipart_upload import find_parts_to_upload, count_completed_parts, calculate_part_size,\
    get_file_chunk, _upload_chunk, _multipart_upload
from synapseclient.utils import MB, GB, make_bogus_binary_file, md5_for_file
from synapseclient.exceptions import SynapseHTTPError
from synapseclient import multipart_upload
from ctypes import c_bool
from mock import patch, MagicMock
import warnings
import concurrent.futures
from synapseclient.dict_object import DictObject


def setup(module):
    module.syn = unit.syn


def test_find_parts_to_upload():
    assert_equals(find_parts_to_upload(""), [])
    assert_equals(find_parts_to_upload("111111111111111111"), [])
    assert_equals(find_parts_to_upload("01010101111111110"), [1, 3, 5, 7, 17])
    assert_equals(find_parts_to_upload("00000"), [1, 2, 3, 4, 5])


def test_count_completed_parts():
    assert_equals(count_completed_parts(""), 0)
    assert_equals(count_completed_parts("01010101111111110"), 12)
    assert_equals(count_completed_parts("00000"), 0)
    assert_equals(count_completed_parts("11111"), 5)


def test_calculate_part_size():

    assert_equals(5*MB, calculate_part_size(fileSize=3*MB, partSize=None, min_part_size=5*MB, max_parts=10000))
    assert_equals(5*MB, calculate_part_size(fileSize=6*MB, partSize=None, min_part_size=5*MB, max_parts=2))
    assert_equals(11*MB / 2.0, calculate_part_size(fileSize=11*MB, partSize=None, min_part_size=5*MB, max_parts=2))
    assert_greater_equal(calculate_part_size(fileSize=100*MB, partSize=None, min_part_size=5*MB, max_parts=2),
                         (100*MB) / 2.0)
    assert_greater_equal(calculate_part_size(fileSize=11*MB+777, partSize=None, min_part_size=5*MB, max_parts=2),
                         (11*MB+777) / 2.0)
    assert_greater_equal(calculate_part_size(fileSize=101*GB+777, partSize=None, min_part_size=5*MB, max_parts=10000),
                         (101*GB+777) / 10000.0)

    # return value should always be an integer (SYNPY-372)
    assert_is_instance(calculate_part_size(fileSize=3*MB+3391), int)
    assert_is_instance(calculate_part_size(fileSize=50*GB+4999), int)
    assert_is_instance(calculate_part_size(fileSize=101*GB+7717, min_part_size=8*MB), int)

    # OK
    assert_equals(calculate_part_size(6*MB, partSize=10*MB, min_part_size=5*MB, max_parts=10000), 10*MB)

    # partSize too small
    assert_raises(ValueError, calculate_part_size, fileSize=100*MB, partSize=1*MB, min_part_size=5*MB, max_parts=10000)

    # too many parts
    assert_raises(ValueError, calculate_part_size, fileSize=21*MB, partSize=1*MB, min_part_size=1*MB, max_parts=20)


def test_chunks():
    # Read a file in chunks, write the chunks out, and compare to the original
    try:
        file_size = 1*MB
        filepath = make_bogus_binary_file(n=file_size)
        chunksize = 64*1024
        nchunks = int(math.ceil(float(file_size) / chunksize))
        with tempfile.NamedTemporaryFile(mode='wb', delete=False) as out:
            for i in range(1, nchunks+1):
                out.write(get_file_chunk(filepath, i, chunksize))
        assert_true(filecmp.cmp(filepath, out.name))
    finally:
        if 'filepath' in locals() and filepath:
            os.remove(filepath)
        if 'out' in locals() and out:
            os.remove(out.name)


def test_upload_chunk__expired_url():
    upload_parts = [{'uploadPresignedUrl': 'https://www.fake.url/fake/news',
                     'partNumber': 420},
                    {'uploadPresignedUrl': 'https://www.google.com',
                     'partNumber': 421},
                    {'uploadPresignedUrl': 'https://rito.pls/',
                     'partNumber': 422},
                    {'uploadPresignedUrl': 'https://never.lucky.gg',
                     'partNumber': 423}
                    ]

    with patch.object(multipart_upload, "_put_chunk",
                      side_effect=SynapseHTTPError("useless message",response=MagicMock(status_code=403))) as mocked_put_chunk, \
                      patch.object(warnings, "warn") as mocked_warn, \
                      patch.object(multipart_upload, '_start_multipart_upload',
                        return_value=DictObject({'partsState': '0', 'uploadId': '1', 'state': 'COMPLETED', 'resultFileHandleId': '1'})), \
                      patch.object(multipart_upload, "_get_presigned_urls", return_value=upload_parts):
        
        file_size = 1*MB
        filepath = make_bogus_binary_file(n=file_size)

        try:
            multipart_upload.multipart_upload(syn, filepath)
        finally:
            if os.path.isfile(filepath):
                os.remove(filepath)

        mocked_warn.assert_called_with('The pre-signed upload URL has expired. Restarting upload...\n')

        # 4 URLs, 7 retries.
        assert mocked_warn.call_count == 28

        # assert _put_chunk was called at least once
        assert_greater_equal(len(mocked_put_chunk.call_args_list), 1)
