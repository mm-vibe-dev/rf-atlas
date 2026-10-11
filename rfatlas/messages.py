"""Metadata from already integrity-validated selected protocol frames.

No text/audio payload decoding, encryption handling or inferred identifiers.
Raw bytes and fields are removed by independent persistence preferences.
"""
SUPPORTED=('DMR','RDS','AX.25','AIS','ADS-B','Fineoffset-WH2','Fineoffset-WH5','Fineoffset-WH24','Fineoffset-WH1050')


def summarize_messages(result):
    messages=[]
    for attempt in result.get('attempts',[]):
        if not attempt.get('protocol_verified'):continue
        for frame in attempt.get('frames',[])[:64]:
            if frame.get('status')!='VERIFIED' or not frame.get('sync_valid') or not frame.get('checksum_valid'):continue
            protocol=frame.get('protocol',attempt.get('protocol'))
            if protocol not in SUPPORTED:continue
            messages.append(dict(schema=1,protocol=protocol,decoder=attempt.get('decoder'),
                source_sha256=result.get('source_sha256'),start_sample=frame.get('start_sample'),
                frame_length=frame.get('frame_length'),sync_valid=True,checksum_valid=True,
                status='VERIFIED_METADATA',fields=dict(frame.get('parsed_fields') or {}),
                raw_hex=frame.get('raw_hex'),content_decoded=False,
                fec='DMR_GOLAY_AND_BPTC_THEN_CRC' if protocol=='DMR' else 'NO_CORRECTION_CLAIMED'))
            if len(messages)>=64:return messages
    return messages
