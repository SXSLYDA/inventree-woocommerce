<?php
/**
 * Signed calls between this site and InvenTree (the inventree-woocommerce plugin).
 *
 *   X-InvenTree-Shop-Timestamp: <unix seconds>
 *   X-InvenTree-Shop-Signature: base64( HMAC-SHA256( secret, "<timestamp>." + body ) )
 *
 * For a quote submission the signed "body" is the JSON payload form field, which
 * lists every uploaded file with its SHA-256, so the files are covered too.
 *
 * @package InvenTreeShop
 */

defined( 'ABSPATH' ) || exit;

class InvenTree_Link {

	const MAX_AGE = 300;

	public static function secret() {
		return (string) get_option( 'inventree_shop_secret', '' );
	}

	public static function base_url() {
		return untrailingslashit( (string) get_option( 'inventree_shop_inventree_url', '' ) );
	}

	/** https only - except plain http to this machine (localhost / 127.0.0.1), for testing. */
	public static function allowed_url( $url ) {
		if ( 0 === strpos( $url, 'https://' ) ) {
			return true;
		}
		$host = wp_parse_url( $url, PHP_URL_HOST );
		return 0 === strpos( $url, 'http://' ) && in_array( $host, array( 'localhost', '127.0.0.1' ), true );
	}

	public static function configured() {
		return '' !== self::secret() && self::allowed_url( self::base_url() );
	}

	public static function sign( $body, $timestamp = null ) {
		$ts = (string) ( null === $timestamp ? time() : (int) $timestamp );
		return array( $ts, base64_encode( hash_hmac( 'sha256', $ts . '.' . $body, self::secret(), true ) ) );
	}

	public static function verify( $body, $timestamp, $signature ) {
		if ( '' === self::secret() || ! $timestamp || ! $signature || ! ctype_digit( (string) $timestamp ) ) {
			return false;
		}
		if ( abs( time() - (int) $timestamp ) > self::MAX_AGE ) {
			return false;
		}
		list( , $expected ) = self::sign( $body, (int) $timestamp );
		return hash_equals( $expected, trim( (string) $signature ) );
	}

	/**
	 * POST signed JSON to an InvenTree plugin endpoint.
	 *
	 * @return array{code:int, body:array}|WP_Error
	 */
	public static function post_json( $path, array $data ) {
		if ( ! self::configured() ) {
			return new WP_Error( 'inventree_not_configured', __( 'The shop isn\'t connected to InvenTree yet.', 'inventree-shop' ) );
		}
		$body              = wp_json_encode( $data );
		list( $ts, $sig )  = self::sign( $body );
		$response          = wp_remote_post(
			self::base_url() . '/plugin/woocommerce/' . ltrim( $path, '/' ),
			array(
				'timeout' => 45,
				'headers' => array(
					'Content-Type'               => 'application/json',
					'X-InvenTree-Shop-Timestamp' => $ts,
					'X-InvenTree-Shop-Signature' => $sig,
				),
				'body'    => $body,
			)
		);
		if ( is_wp_error( $response ) ) {
			return $response;
		}
		$decoded = json_decode( wp_remote_retrieve_body( $response ), true );
		return array( 'code' => (int) wp_remote_retrieve_response_code( $response ), 'body' => is_array( $decoded ) ? $decoded : array() );
	}

	/**
	 * POST a quote request: signed JSON payload + files, streamed with cURL.
	 *
	 * @param array $payload Payload (already includes the file list with hashes).
	 * @param array $files   [ ['upload' => 'file_0', 'path' => '/tmp/x', 'name' => 'a.pdf'], ... ].
	 * @return array{code:int, body:array}|WP_Error
	 */
	public static function post_quote( array $payload, array $files ) {
		if ( ! self::configured() ) {
			return new WP_Error( 'inventree_not_configured', __( 'The shop isn\'t connected to InvenTree yet.', 'inventree-shop' ) );
		}
		if ( ! function_exists( 'curl_init' ) ) {
			return new WP_Error( 'inventree_no_curl', 'PHP cURL is required to send quote files.' );
		}
		$json             = wp_json_encode( $payload );
		list( $ts, $sig ) = self::sign( $json );

		// Each file travels in its own field (file_0, file_1, ...); the payload's file list
		// says which upload field holds which item's file, with its size and SHA-256.
		$fields = array( 'payload' => $json );
		foreach ( $files as $file ) {
			$fields[ $file['upload'] ] = new CURLFile( $file['path'], 'application/octet-stream', $file['name'] );
		}

		$ch = curl_init( self::base_url() . '/plugin/woocommerce/quotes/submit/' );
		curl_setopt_array(
			$ch,
			array(
				CURLOPT_POST           => true,
				CURLOPT_POSTFIELDS     => $fields,
				CURLOPT_RETURNTRANSFER => true,
				CURLOPT_TIMEOUT        => 300,
				CURLOPT_HTTPHEADER     => array( 'X-InvenTree-Shop-Timestamp: ' . $ts, 'X-InvenTree-Shop-Signature: ' . $sig ),
			)
		);
		$raw  = curl_exec( $ch );
		$code = (int) curl_getinfo( $ch, CURLINFO_HTTP_CODE );
		$err  = curl_error( $ch );
		curl_close( $ch );
		if ( false === $raw ) {
			return new WP_Error( 'inventree_unreachable', $err ?: 'Could not reach InvenTree.' );
		}
		$decoded = json_decode( $raw, true );
		return array( 'code' => $code, 'body' => is_array( $decoded ) ? $decoded : array() );
	}
}
