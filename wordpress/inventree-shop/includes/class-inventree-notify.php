<?php
/**
 * POST /wp-json/inventree-shop/v1/notify - signed notifications from InvenTree,
 * turned into customer emails (quote ready, sample received, declined, expiring, expired).
 *
 * @package InvenTreeShop
 */

defined( 'ABSPATH' ) || exit;

class InvenTree_Shop_Notify {

	public static function init() {
		add_action( 'rest_api_init', array( __CLASS__, 'routes' ) );
	}

	public static function routes() {
		register_rest_route(
			'inventree-shop/v1',
			'/notify',
			array(
				'methods'             => 'POST',
				'callback'            => array( __CLASS__, 'handle' ),
				'permission_callback' => array( __CLASS__, 'verify' ),
			)
		);
	}

	public static function verify( WP_REST_Request $request ) {
		return InvenTree_Link::verify( $request->get_body(), $request->get_header( 'X-InvenTree-Shop-Timestamp' ), $request->get_header( 'X-InvenTree-Shop-Signature' ) );
	}

	/** Subject, heading and HTML body for one notification. Returns null for unknown events. */
	public static function message( array $data ) {
		$ref   = isset( $data['reference'] ) ? (string) $data['reference'] : '';
		$link  = '<p><a href="' . esc_url( InvenTree_Shop_Quotes::account_url( $ref ) ) . '">' . esc_html__( 'View your quote', 'inventree-shop' ) . '</a></p>';
		$until = ! empty( $data['valid_until'] ) ? date_i18n( wc_date_format(), strtotime( $data['valid_until'] ) ) : '';
		$phone = InvenTree_Shop_Settings::get( 'phone', '' );
		$call  = $phone ? '<p>' . esc_html( sprintf( /* translators: %s: phone */ __( 'Questions? Call us on %s.', 'inventree-shop' ), $phone ) ) . '</p>' : '';

		switch ( isset( $data['event'] ) ? $data['event'] : '' ) {
			case 'quote_sent':
				$total = isset( $data['total'] ) ? InvenTree_Shop_Quotes::money( $data['total'], isset( $data['currency'] ) ? $data['currency'] : '' ) : '';
				return array(
					/* translators: %s: reference */
					sprintf( __( 'Your quote %s is ready', 'inventree-shop' ), $ref ),
					__( 'Your quote is ready', 'inventree-shop' ),
					'<p>' . esc_html( sprintf( /* translators: %s: reference */ __( 'Your quote %s is ready.', 'inventree-shop' ), $ref ) ) . '</p>'
						. ( $total ? '<p>' . esc_html__( 'Total (ex GST):', 'inventree-shop' ) . ' ' . wp_kses_post( $total ) . '</p>' : '' )
						. ( $until ? '<p>' . esc_html( sprintf( /* translators: %s: date */ __( 'Valid until %s. You can accept all or part of it online.', 'inventree-shop' ), $until ) ) . '</p>' : '' )
						. ( ! empty( $data['pdf'] ) ? '<p>' . esc_html__( 'The quote is attached as a PDF.', 'inventree-shop' ) . '</p>' : '' )
						. $link . $call,
				);
			case 'sample_received':
				return array(
					/* translators: %s: reference */
					sprintf( __( 'We\'ve received your sample for %s', 'inventree-shop' ), $ref ),
					__( 'Sample received', 'inventree-shop' ),
					'<p>' . esc_html( sprintf( /* translators: %s: reference */ __( 'Your sample for quote request %s has arrived. We\'ll be in touch with your quote.', 'inventree-shop' ), $ref ) ) . '</p>' . $link,
				);
			case 'quote_declined':
				return array(
					/* translators: %s: reference */
					sprintf( __( 'About your quote request %s', 'inventree-shop' ), $ref ),
					__( 'About your quote request', 'inventree-shop' ),
					'<p>' . esc_html( sprintf( /* translators: %s: reference */ __( 'Unfortunately we can\'t quote request %s.', 'inventree-shop' ), $ref ) ) . '</p>'
						. ( ! empty( $data['reason'] ) ? '<p>' . esc_html( $data['reason'] ) . '</p>' : '' ) . $call,
				);
			case 'quote_expiring':
				return array(
					/* translators: %s: reference */
					sprintf( __( 'Your quote %s expires soon', 'inventree-shop' ), $ref ),
					__( 'Your quote expires soon', 'inventree-shop' ),
					'<p>' . esc_html( sprintf( /* translators: 1: reference, 2: date */ __( 'Quote %1$s is valid until %2$s.', 'inventree-shop' ), $ref, $until ) ) . '</p>' . $link . $call,
				);
			case 'quote_expired':
				return array(
					/* translators: %s: reference */
					sprintf( __( 'Your quote %s has expired', 'inventree-shop' ), $ref ),
					__( 'Quote expired', 'inventree-shop' ),
					'<p>' . esc_html( sprintf( /* translators: %s: reference */ __( 'Quote %s has expired. Request a new quote or call us if you still need it.', 'inventree-shop' ), $ref ) ) . '</p>' . $call,
				);
		}
		return null;
	}

	public static function handle( WP_REST_Request $request ) {
		$data    = json_decode( $request->get_body(), true );
		$data    = is_array( $data ) ? $data : array();
		$message = self::message( $data );
		if ( null === $message ) {
			return new WP_REST_Response( array( 'detail' => 'ignored' ), 200 );
		}
		$user  = ! empty( $data['customer_id'] ) ? get_userdata( (int) $data['customer_id'] ) : null;
		$email = $user ? $user->user_email : ( isset( $data['email'] ) ? sanitize_email( $data['email'] ) : '' );
		if ( ! $email ) {
			return new WP_REST_Response( array( 'detail' => 'no recipient' ), 200 );
		}
		list( $subject, $heading, $html ) = $message;

		// "Quote ready": attach the PDF (fetched from InvenTree, signed) when InvenTree offers it.
		$attachments = array();
		if ( 'quote_sent' === $data['event'] && ! empty( $data['pdf'] ) && ! empty( $data['customer_id'] ) ) {
			$pdf = InvenTree_Link::quote_pdf( (int) $data['customer_id'], (string) $data['reference'] );
			if ( ! is_wp_error( $pdf ) ) {
				$dir  = trailingslashit( get_temp_dir() ) . 'inventree-quote-' . wp_generate_password( 12, false );
				wp_mkdir_p( $dir );
				$path = $dir . '/Quote-' . sanitize_file_name( $data['reference'] ) . '.pdf';
				if ( false !== file_put_contents( $path, $pdf ) ) { // phpcs:ignore WordPress.WP.AlternativeFunctions
					$attachments[] = $path;
				}
			}
		}
		InvenTree_Shop_Quotes::send_email( $email, $subject, $heading, $html, $attachments );
		foreach ( $attachments as $path ) {
			wp_delete_file( $path );
			@rmdir( dirname( $path ) ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
		}
		return new WP_REST_Response( array( 'detail' => 'sent', 'attached' => count( $attachments ) ), 200 );
	}
}
